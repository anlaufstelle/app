"""WCAG-2.1-AA-Gate: axe-core ueber die Hauptseiten + Skip-Link-Funktionstest.

Refs https://github.com/anlaufstelle/app/issues/1059 (Stufe 1, „Gate first").

Aufbau
======
* ``run_axe`` (``_axe.py``) injiziert die vendorte ``axe.min.js`` same-origin und
  liefert ALLE Violations jeder Impact-Stufe zurueck.
* Gate ist ``assert_within_baseline``: ``critical``/``serious`` duerfen nur
  auftreten, wenn sie in ``a11y_baseline.json`` unter dem ``page_key`` mit
  mindestens dieser Knotenzahl stehen — und ein Baseline-Eintrag, der nicht mehr
  auftritt, ist ebenfalls rot (er ist zu entfernen). Die Baseline schrumpft
  dadurch monoton; Ziel ist ``{}``.
* ``AXE_REPORT_PATH=<datei.json>`` schreibt zusaetzlich JEDEN Befund jeder
  Impact-Stufe (inkl. ``moderate``/``minor``) als Artefakt — Grundlage fuer den
  Audit-Fan-out und fuer das Pinnen der Baseline.

Warum die Suite seriell laeuft: sie haengt an ``authenticated_page`` & Co. und
damit am gemeinsamen E2E-Server; ausserdem schreibt das Report-Artefakt
read-modify-write ohne Lock. ``make test-a11y`` setzt deshalb kein ``-n``.

Bewusst NICHT in der Matrix
===========================
* ``/admin-mgmt/`` — Django/Unfold-Fremd-UI, nicht unser Markup.
* ``/uebergabe/`` — reiner ``RedirectView`` auf ``/?view=uebergabe``, kein
  eigenes Template (verifiziert gegen ``core/urls/__init__.py:192``).
* Die 18 eigenstaendigen Seiten ohne ``base.html`` (Login, Passwort-Reset, MFA,
  Lockout-Recovery, Fehler-/Wartungsseiten) sind mit ``/login/`` und
  ``/password-reset/`` nur STICHPROBENARTIG vertreten — die Familie als Ganzes
  gehoert in den Audit-Fan-out.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from ._axe import assert_within_baseline, load_baseline, record_report, run_axe
from ._helpers import django_shell

pytestmark = [pytest.mark.e2e, pytest.mark.a11y]

BASELINE_PATH = Path(__file__).with_name("a11y_baseline.json")

# (page_key, pfad, seitenspezifischer Ready-Selektor)
#
# Der Ready-Selektor ist Pflicht und bewusst seitenspezifisch: ``goto`` mit
# ``domcontentloaded`` sagt nichts darueber, ob der HTMX-/Alpine-Inhalt steht,
# und ``networkidle`` ist im Repo verboten (Session-Saves resetten den Timer).
# Ein axe-Lauf auf einem halb gerenderten DOM waere still falsch-gruen.
ANONYMOUS_PAGES = [
    ("login", "/login/", "input[name='username']"),
    ("password_reset", "/password-reset/", "input[name='email']"),
]

AUTHENTICATED_PAGES = [
    ("zeitstrom", "/", "[data-testid='zeitstrom-cockpit']"),
    # /start/ ist eine Weiche: die Anwendungsbetreuung landet auf /verwaltung/
    # (Refs #1813) — gescannt wird also die Verwaltungs-Uebersicht.
    ("start_facility_admin", "/start/", "[data-testid='subnav-verwaltung']"),
    ("client_list", "/clients/", "#main-content h1:has-text('Personen')"),
    ("client_create", "/clients/new/", "#main-content h1:has-text('Person anlegen')"),
    ("case_list", "/cases/", "#main-content h1:has-text('Fälle')"),
    ("event_create", "/events/new/", "#main-content h1:has-text('Kontakt dokumentieren')"),
    ("workitem_inbox", "/workitems/", "#main-content h1:has-text('Aufgaben')"),
    ("attachment_list", "/attachments/", "#main-content h1:has-text('Dateien')"),
    # Charts kommen per HTMX + Chart.js nach — auf das Canvas warten.
    ("statistics", "/statistics/", "#chart-contacts"),
    ("audit_log", "/audit/", "#main-content h1:has-text('Audit-Log')"),
    ("retention", "/retention/", "#main-content h1:has-text('Löschfristen')"),
    ("search", "/search/?q=k", "#main-content h1:has-text('Suche')"),
    ("account", "/account/", "#main-content h1:has-text('Mein Profil')"),
    ("dsgvo", "/dsgvo/", "#main-content h1:has-text('DSGVO-Dokumentationspaket')"),
    ("offline_conflicts", "/offline/conflicts/", "[data-testid='conflict-list-view']"),
]

SUPER_ADMIN_PAGES = [
    ("system_dashboard", "/system/", "[data-testid='system-cron-card']"),
    ("system_compliance", "/system/compliance/", "[data-testid='compliance-group-hintergrundjobs']"),
]

# Seiten fuer den Skip-Link-Funktionstest: alle drei erben von ``base.html``
# (dort liegt der Skip-Link, ``base.html:59-61``, Ziel ``base.html:117``).
# ``/login/`` steht hier bewusst NICHT — die eigenstaendigen Auth-Templates
# haben weder Skip-Link noch ``<main id="main-content">`` (Befund, siehe
# Modul-Docstring und Lane-Bericht).
SKIP_LINK_PAGES = ["/", "/events/new/", "/clients/"]


@pytest.fixture(scope="session")
def a11y_baseline():
    """Bestandsschutz-Baseline (``{page_key: {rule_id: knoten}}``)."""
    return load_baseline(BASELINE_PATH)


@pytest.fixture(scope="session")
def a11y_report_path():
    """Zieldatei fuer das Voll-Artefakt, falls ``AXE_REPORT_PATH`` gesetzt ist.

    Die Datei wird zu Sessionbeginn geleert, damit Befunde eines frueheren
    Laufs nicht als aktuelle Ergebnisse weiterleben.
    """
    raw = os.environ.get("AXE_REPORT_PATH")
    if not raw:
        return None
    path = Path(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    return path


@pytest.fixture
def anonymous_page(base_url, browser):
    """Frischer, NICHT eingeloggter Kontext (Login-/Reset-Seiten)."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)
    yield page
    context.close()


@pytest.fixture(scope="session")
def detail_pks(e2e_env):
    """Je eine stabile PK fuer Personen-, Fall- und Ereignis-Detailseite.

    Ueber die ORM statt ueber einen ``.first``-Klick auf einer Liste: der
    Selektor-Guard (``test_architecture_guards_audit.py``,
    ``TestE2ESelectorStabilityGuard``) verbietet die Listen-Variante zu Recht —
    die Reihenfolge der Seed-Listen ist nicht garantiert. (Der Guard ist ein
    reiner Textscan: schon die ERWAEHNUNG des Musters in einem Docstring
    zaehlt er mit — deshalb steht es hier umschrieben.) ``order_by('created_at')``
    ist dagegen deterministisch, und der Seed ist mit ``random.seed(42)`` fest.
    Ein einziger Subprozess fuer die ganze Session.
    """
    script = (
        "from core.models import Case, Client, Event;"
        " pick = lambda qs: qs.active().order_by('created_at').values_list('pk', flat=True).first();"
        " print('client', pick(Client.objects));"
        " print('case', pick(Case.objects));"
        " print('event', pick(Event.objects))"
    )
    pks = {}
    for line in django_shell(e2e_env, script):
        key, _, value = line.partition(" ")
        pks[key] = value.strip()
    missing = [k for k in ("client", "case", "event") if not pks.get(k) or pks[k] == "None"]
    assert not missing, f"Seed liefert keine Detailobjekte fuer {missing} — Matrix nicht scanbar: {pks}"
    return pks


def _scan(page, base_url, page_key, baseline, report_path):
    """axe laufen lassen, Artefakt schreiben, gegen die Baseline pruefen."""
    result = run_axe(page, base_url)
    record_report(report_path, page_key, result)
    assert_within_baseline(page_key, result, baseline)


def _open(page, base_url, path, ready_selector):
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
    expect(page.locator(ready_selector)).to_be_visible(timeout=15000)


class TestAxeSeitenMatrix:
    """Ein axe-Lauf je Hauptseite, gegen die Bestandsschutz-Baseline."""

    @pytest.mark.parametrize(("page_key", "path", "ready"), ANONYMOUS_PAGES, ids=[p[0] for p in ANONYMOUS_PAGES])
    def test_anonymous_pages(self, anonymous_page, base_url, a11y_baseline, a11y_report_path, page_key, path, ready):
        _open(anonymous_page, base_url, path, ready)
        _scan(anonymous_page, base_url, page_key, a11y_baseline, a11y_report_path)

    @pytest.mark.parametrize(
        ("page_key", "path", "ready"), AUTHENTICATED_PAGES, ids=[p[0] for p in AUTHENTICATED_PAGES]
    )
    def test_authenticated_pages(
        self, authenticated_page, base_url, a11y_baseline, a11y_report_path, page_key, path, ready
    ):
        _open(authenticated_page, base_url, path, ready)
        _scan(authenticated_page, base_url, page_key, a11y_baseline, a11y_report_path)

    @pytest.mark.parametrize(("page_key", "path", "ready"), SUPER_ADMIN_PAGES, ids=[p[0] for p in SUPER_ADMIN_PAGES])
    def test_super_admin_pages(
        self, super_admin_page, base_url, a11y_baseline, a11y_report_path, page_key, path, ready
    ):
        _open(super_admin_page, base_url, path, ready)
        _scan(super_admin_page, base_url, page_key, a11y_baseline, a11y_report_path)

    def test_staff_dashboard(self, staff_page, base_url, a11y_baseline, a11y_report_path):
        """Fachkraft: ``/start/`` leitet auf den Zeitstrom um (Refs #1124).

        Eigener ``page_key``, obwohl das Ziel dieselbe URL wie ``zeitstrom`` ist:
        Navigation, Sichtbarkeiten und Aktionsleisten haengen an der Rolle, das
        gerenderte Markup ist also ein anderes.
        """
        _open(staff_page, base_url, "/start/", "[data-testid='zeitstrom-cockpit']")
        _scan(staff_page, base_url, "start_staff", a11y_baseline, a11y_report_path)

    def test_assistant_zeitstrom(self, assistant_page, base_url, a11y_baseline, a11y_report_path):
        """Assistenz: dieselbe Seite mit der kleinsten Rechtemenge."""
        _open(assistant_page, base_url, "/", "[data-testid='zeitstrom-cockpit']")
        _scan(assistant_page, base_url, "zeitstrom_assistant", a11y_baseline, a11y_report_path)


class TestAxeDetailseiten:
    """Detailansichten — die Seiten mit der hoechsten Markup-Dichte."""

    def test_client_detail(self, authenticated_page, base_url, detail_pks, a11y_baseline, a11y_report_path):
        _open(authenticated_page, base_url, f"/clients/{detail_pks['client']}/", "#main-content h1")
        _scan(authenticated_page, base_url, "client_detail", a11y_baseline, a11y_report_path)

    def test_case_detail(self, authenticated_page, base_url, detail_pks, a11y_baseline, a11y_report_path):
        _open(authenticated_page, base_url, f"/cases/{detail_pks['case']}/", "#main-content h1")
        _scan(authenticated_page, base_url, "case_detail", a11y_baseline, a11y_report_path)

    def test_event_detail(self, authenticated_page, base_url, detail_pks, a11y_baseline, a11y_report_path):
        _open(authenticated_page, base_url, f"/events/{detail_pks['event']}/", "#main-content h1")
        _scan(authenticated_page, base_url, "event_detail", a11y_baseline, a11y_report_path)


class TestAxeFormularzustaende:
    """Das Ereignisformular in seinen beiden interessanten Zustaenden.

    Der Leerzustand (``event_create``) steckt in der Seiten-Matrix oben; hier
    kommen die beiden Zustaende dazu, in denen erst der zusaetzliche DOM
    entsteht, an dem a11y typischerweise bricht: dynamische Felder und
    gerenderte Formularfehler.
    """

    def _select_doc_type_and_wait(self, page, label, ready_selector):
        """Dokumenttyp waehlen und auf den FRISCH geswappten Feldblock warten.

        Muster aus ``test_file_vault.py:31-49`` (bewusst dupliziert statt aus
        einer fremden Testdatei importiert): ``/events/new/`` rendert die Felder
        des Default-Typs bereits server-seitig, ein nacktes
        ``wait_for_selector`` matcht deshalb den VOR-Swap-Knoten. Alt-Bestand
        stempeln, nur den ungestempelten Nachfolger akzeptieren.
        """
        page.evaluate(
            "() => document.querySelectorAll('#dynamic-fields > *')"
            ".forEach((el) => el.setAttribute('data-pre-swap', '1'))"
        )
        page.select_option('select[name="document_type"]', label=label)
        page.wait_for_selector(
            f"css=#dynamic-fields > :not([data-pre-swap]) >> {ready_selector}",
            timeout=10000,
        )

    def test_event_form_with_dynamic_fields(self, authenticated_page, base_url, a11y_baseline, a11y_report_path):
        """Nach der Dokumenttyp-Wahl: der per HTMX eingeswappte Feldblock."""
        page = authenticated_page
        _open(page, base_url, "/events/new/", "#main-content h1:has-text('Kontakt dokumentieren')")
        self._select_doc_type_and_wait(page, "Beratungsgespräch", "text=Scan/Bescheid")
        _scan(page, base_url, "event_create_dynamic_fields", a11y_baseline, a11y_report_path)

    def test_event_form_validation_errors(self, authenticated_page, base_url, a11y_baseline, a11y_report_path):
        """Fehlerzustand: Pflichtfeld ``Zeitpunkt`` leer abgesendet.

        ``noValidate`` wird bewusst gesetzt: geprueft werden soll die
        SERVER-gerenderte Fehlerdarstellung (``role="alert"``-Liste,
        ``aria-invalid``/``aria-describedby`` aus dem ``aria_field``-Tag). Ohne
        das Flag faengt die HTML5-Validierung das Formular ab und die Seite,
        um die es geht, entsteht nie.
        """
        page = authenticated_page
        _open(page, base_url, "/events/new/", "#main-content h1:has-text('Kontakt dokumentieren')")
        page.evaluate("() => { document.getElementById('event-create-form').noValidate = true; }")
        page.fill("#id_occurred_at", "")
        page.locator("#event-submit-btn").click()
        expect(page.locator("#id_occurred_at-error")).to_be_visible(timeout=15000)
        _scan(page, base_url, "event_create_errors", a11y_baseline, a11y_report_path)


class TestSkipLink:
    """WCAG 2.4.1: der Skip-Link ist erstes Tab-Ziel und springt in den Inhalt.

    Kein axe-Test — axe prueft das Vorhandensein eines Bypass-Mechanismus, aber
    nicht, ob er tatsaechlich FUNKTIONIERT. Genau das ist hier der Punkt
    (alter Plan P6).
    """

    @pytest.mark.parametrize("path", SKIP_LINK_PAGES)
    def test_skip_link_is_first_tab_stop_and_jumps_to_main(self, authenticated_page, base_url, path):
        page = authenticated_page
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        expect(page.locator("#main-content")).to_be_attached()

        page.keyboard.press("Tab")
        focused_href = page.evaluate("() => document.activeElement && document.activeElement.getAttribute('href')")
        assert focused_href == "#main-content", (
            f"Erstes Tab-Ziel auf {path} ist nicht der Skip-Link, sondern "
            f"{page.evaluate('() => document.activeElement && document.activeElement.outerHTML')}"
        )

        page.keyboard.press("Enter")
        assert page.evaluate("() => window.location.hash") == "#main-content", (
            f"Skip-Link auf {path} hat den Fokuspunkt nicht auf #main-content gesetzt (URL: {page.url})"
        )

        # Der eigentliche Nutzen: das NAECHSTE Tab landet im Inhalt, nicht
        # wieder am Anfang der Navigation. Ein Skip-Link, der nur den Hash
        # setzt, hat die Navigation nicht uebersprungen.
        page.keyboard.press("Tab")
        assert page.evaluate("() => !!(document.activeElement && document.activeElement.closest('#main-content'))"), (
            f"Nach dem Skip-Link auf {path} liegt der Fokus ausserhalb von #main-content: "
            f"{page.evaluate('() => document.activeElement && document.activeElement.outerHTML')}"
        )
