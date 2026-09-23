"""E2E: Trefferansage der Personen-/Fallliste nennt den Gesamtwert (Refs #2155).

Abgrenzung zu ``test_a11y_semantics.py::TestListResultAnnouncement`` (Unit,
Django-Test-Client): dort steht der Vertrag am gerenderten HTML (Attribute,
Struktur, exakter Text bei genau einem Datensatz). Hier geht es um den echten
Browser-Pfad — ein HTMX-Filterwechsel im laufenden DOM: genau eine
``role="status"``-Region trägt danach den Gesamtwert, kein zweites Element mit
derselben ID (Doppelansage, WCAG 4.1.1), und der Fokus bleibt am
auslösenden Bedienelement (``data-hx-no-focus``/Live-Suche, unverändert).

Die erwarteten Texte werden bewusst NICHT aus der (kleinen, ``small``-Scale)
Seed-Datenmenge geraten, sondern über ``django_shell`` mit exakt derselben
Paginator-Abfrage wie die jeweilige ListView berechnet (``core/views/
clients.py::ClientListView``, ``core/views/cases.py::CaseListView``) — der
Test bleibt damit unabhängig von der tatsächlichen Trefferzahl korrekt und
prüft genau den Regressionsfall aus #2155 (Gesamtwert statt gezählter
DOM-Zeilen der sichtbaren Seite).
"""

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell

pytestmark = pytest.mark.e2e

_CLIENT_STAGE_PAGINATOR = """
from django.core.paginator import Paginator
from core.constants import DEFAULT_PAGE_SIZE
from core.models import Client, Facility
f = Facility.objects.get(name="Hauptstelle")
qs = (
    Client.objects.for_facility(f)
    .filter(is_active=True, is_deleted=False, contact_stage="identified")
    .order_by("pseudonym")
)
p = Paginator(qs, DEFAULT_PAGE_SIZE)
print(p.count)
print(p.num_pages)
"""

_CASE_STATUS_PAGINATOR = """
from django.core.paginator import Paginator
from core.constants import DEFAULT_PAGE_SIZE
from core.models import Facility
from core.services.case.cases import cases_with_live_client
f = Facility.objects.get(name="Hauptstelle")
qs = (
    cases_with_live_client()
    .for_facility(f)
    .filter(status="open")
    .select_related("client", "lead_user")
    .order_by("-created_at")
)
p = Paginator(qs, DEFAULT_PAGE_SIZE)
print(p.count)
print(p.num_pages)
"""


def _paginator_count_and_pages(e2e_env, script):
    lines = django_shell(e2e_env, script)
    return int(lines[0]), int(lines[1])


def _expected_client_text(total, pages):
    if total == 0:
        return "Keine Person gefunden."
    if total == 1:
        return "1 Person gefunden."
    return f"{total} Personen gefunden, Seite 1 von {pages}."


def _expected_case_text(total, pages):
    if total == 0:
        return "Kein Fall gefunden."
    if total == 1:
        return "1 Fall gefunden."
    return f"{total} Fälle gefunden, Seite 1 von {pages}."


class TestListAnnouncementTotal:
    """Filterwechsel → OOB-Statuszeile nennt paginator.count, nicht die
    Zahl der sichtbaren Zeilen."""

    def test_client_filter_change_announces_the_real_total(self, authenticated_page, base_url, e2e_env):
        page = authenticated_page
        page.goto(f"{base_url}/clients/")

        region = page.locator("#client-list-status")
        expect(region).to_have_count(1)

        page.select_option("select[name='stage']", "identified")

        total, pages = _paginator_count_and_pages(e2e_env, _CLIENT_STAGE_PAGINATOR)
        expected = _expected_client_text(total, pages)

        # Genau EINE Region mit dieser ID — kein zweites Element durch den
        # OOB-Swap (WCAG 4.1.1, Doppelansage).
        expect(region).to_have_count(1)
        expect(region).to_have_text(expected, timeout=10000)
        assert region.get_attribute("role") == "status"
        assert region.get_attribute("aria-live") == "polite"
        # "Kein Fokusverlust" (R-A-3): das OOB-Fragment liegt AUSSERHALB des
        # Swap-Ziels und wird von focus-management.js gar nicht erst
        # angefasst (nur der reguläre ``htmx:afterSwap``-Ziel-Container
        # durchläuft die Fokuswahl) — das ist hier nicht neu beobachtbar
        # geprüft (Playwright/CDP `select_option` blurt den nativen
        # `<select>` selbst schon vor jedem Swap auf `<body>`, unabhängig
        # von #2155). Der eigentliche Nicht-Diebstahl-Nachweis steht in
        # ``tests/js/focus-management.test.mjs`` ("data-hx-no-focus am
        # Swap-Ziel unterdrueckt auch Stufe 2") und in der Live-Suche unten,
        # wo ein Text-Feld sein Fokus tatsächlich behält.

    def test_case_filter_change_announces_the_real_total(self, authenticated_page, base_url, e2e_env):
        page = authenticated_page
        page.goto(f"{base_url}/cases/")

        region = page.locator("#case-list-status")
        expect(region).to_have_count(1)

        page.select_option("select[name='status']", "open")

        total, pages = _paginator_count_and_pages(e2e_env, _CASE_STATUS_PAGINATOR)
        expected = _expected_case_text(total, pages)

        expect(region).to_have_count(1)
        expect(region).to_have_text(expected, timeout=10000)
        assert region.get_attribute("role") == "status"
        assert region.get_attribute("aria-live") == "polite"

    def test_client_search_with_no_matches_announces_zero(self, authenticated_page, base_url):
        """Deterministischer Leerzustand — unabhaengig von der Seed-Groesse:
        ein garantiert nicht vorkommendes Pseudonym-Fragment liefert immer 0
        Treffer."""
        page = authenticated_page
        page.goto(f"{base_url}/clients/")

        # Nicht ``input[name='q']`` — die globale Kopf-Suche (Desktop UND
        # Mobil, je ein verstecktes Duplikat im DOM) traegt denselben Namen;
        # das listen-eigene Suchfeld ist ueber sein ``aria-label`` eindeutig.
        # ``fill()`` loest kein ``keyup`` aus (nur ``input``/``change``) — der
        # Filter haengt an ``hx-trigger="keyup changed delay:300ms"``, also
        # ueber ``press_sequentially`` echte Tastendruecke simulieren.
        search = page.locator('input[aria-label="Suchbegriff"]')
        search.click()
        search.press_sequentially("e2e-2155-garantiert-kein-treffer", delay=20)

        region = page.locator("#client-list-status")
        expect(region).to_have_count(1)
        expect(region).to_have_text("Keine Person gefunden.", timeout=10000)

        # Live-Suche (Refs #1339, Abgrenzung Live-Suche in focus-management.js):
        # der Fokus bleibt im Suchfeld, die Ansage darf ihn nicht stehlen.
        assert page.evaluate("document.activeElement.getAttribute('name')") == "q"


# Befund MITTEL (a-codex-review.md § C): die kleine
# ``small``-Scale-Seed-Groesse (<= 7 Klientel, DEFAULT_PAGE_SIZE = 25) laesst
# ``paginator.count`` und die Zahl sichtbarer Zeilen in JEDEM bisherigen Test
# zufaellig zusammenfallen — eine fehlerhafte Reimplementierung mit
# ``len(page)``/gezaehlten DOM-Zeilen haette denselben erwarteten Text
# geliefert. ``_BULK_COUNT`` erzwingt genau den Unterschied: mehr Datensaetze
# als eine Seite fasst.
_BULK_MARKER = "E2E2155Bulk-"
_BULK_COUNT = 30  # > DEFAULT_PAGE_SIZE (25) -> genau 2 Seiten (ceil(30/25))

_CREATE_BULK_CLIENTS = f"""
from core.models import Client, Facility, User
f = Facility.objects.get(name="Hauptstelle")
admin = User.objects.get(username="admin")
created = 0
for i in range({_BULK_COUNT}):
    Client.objects.create(
        facility=f,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym=f"{_BULK_MARKER}{{i:03d}}",
        created_by=admin,
    )
    created += 1
print(created)
"""

# Aufraeumen wie in ``test_dst_fold.py`` (``_retire_events_of_the_double_hour``):
# ``is_deleted=True`` statt Hard-Delete (derselbe Soft-Delete-Weg wie die
# Anwendung selbst nutzt), gefiltert ueber den fuer diese Datei eindeutigen
# Pseudonym-Marker -- trifft nichts, was ein anderer Test angelegt hat, auch
# wenn dieser Test vorzeitig abbricht.
_RETIRE_BULK_CLIENTS = f"""
from core.models import Client
n = Client.objects.filter(pseudonym__startswith="{_BULK_MARKER}").update(is_deleted=True)
print(n)
"""

_BULK_SEARCH_PAGINATOR = f"""
from django.core.paginator import Paginator
from core.constants import DEFAULT_PAGE_SIZE
from core.models import Client, Facility
f = Facility.objects.get(name="Hauptstelle")
qs = (
    Client.objects.for_facility(f)
    .filter(is_active=True, is_deleted=False, pseudonym__ilike="{_BULK_MARKER}")
    .order_by("pseudonym")
)
p = Paginator(qs, DEFAULT_PAGE_SIZE)
print(p.count)
print(p.num_pages)
"""


class TestListAnnouncementAcrossMultiplePages:
    """Refs #2155: Gesamtwert != sichtbare Zeilen, echt bewiesen."""

    @pytest.fixture(autouse=True)
    def _bulk_clients(self, e2e_env):
        created = int(django_shell(e2e_env, _CREATE_BULK_CLIENTS)[-1])
        assert created == _BULK_COUNT, "Vorbedingung: alle Bulk-Klienten muessen angelegt worden sein"
        yield
        django_shell(e2e_env, _RETIRE_BULK_CLIENTS)

    def test_client_search_across_two_pages_announces_the_total_not_the_visible_rows(
        self, authenticated_page, base_url, e2e_env
    ):
        page = authenticated_page
        page.goto(f"{base_url}/clients/")

        # Marker-Suche statt Stufen-Filter: unabhaengig von jeder Seed-Menge
        # eindeutig auf genau die hier angelegten Datensaetze (kein anderes
        # Pseudonym im Bestand enthaelt diesen String).
        search = page.locator('input[aria-label="Suchbegriff"]')
        search.click()
        search.press_sequentially(_BULK_MARKER, delay=20)

        total, pages = _paginator_count_and_pages(e2e_env, _BULK_SEARCH_PAGINATOR)
        assert total == _BULK_COUNT, "Marker darf keine Kollision mit Seed-Pseudonymen haben"
        assert pages == 2, "Der Test soll genau den Mehrseiten-Fall pruefen"

        region = page.locator("#client-list-status")
        expect(region).to_have_count(1)
        expect(region).to_have_text(f"{total} Personen gefunden, Seite 1 von {pages}.", timeout=10000)
        assert region.get_attribute("role") == "status"
        assert region.get_attribute("aria-live") == "polite"

        # Die entscheidende Unterscheidung: Seite 1 zeigt hoechstens
        # DEFAULT_PAGE_SIZE Zeilen, die Ansage nennt aber den STRENG
        # GROESSEREN Gesamtwert — eine Reimplementierung ueber die sichtbare
        # Zeilenzahl waere hier nachweisbar falsch Review MITTEL).
        visible_rows = page.locator("[data-testid='client-row']").count()
        assert 0 < visible_rows < total, (visible_rows, total)
