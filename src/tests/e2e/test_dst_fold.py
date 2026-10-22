"""E2E: Okkurrenz-Auswahl fuer die doppelte Stunde am Rueckfalltag (Refs #2127).

Der Rueckfalltag (Europe/Berlin: letzter Sonntag im Oktober, 03:00 -> 02:00)
laesst die Wanduhrzeit 02:00-02:59 zweimal ablaufen. Bis #2127 kostete das die
reale zweite Stunde: das Formular lehnte die Eingabe ab (#2117), die Fachkraft
wich auf 01:59/03:00 aus und vermerkte die Wahrheit im Freitext.

Diese Datei prueft die Mechanik dort, wo sie stattfindet — im Browser, ueber
zwei echte POSTs:

1. Uhrzeit im Doppelfenster absenden -> das Formular kommt mit einem
   zusaetzlichen Select ``occurred_at_fold`` und einer Rueckfrage zurueck;
2. Sommer- oder Winterzeit waehlen und erneut absenden -> das Ereignis wird
   gespeichert, und der gespeicherte UTC-Zeitpunkt unterscheidet die beiden
   Lesarten um genau eine Stunde.

Der Sichtbarkeitswechsel ist rein serverseitig (kein JavaScript, kein
``hidden``-Attribut): ausserhalb des Grenzfalls existiert das Select gar nicht
erst im DOM. Genau deshalb tragen die Tests hier auch den a11y-Nachweis des
Tickets (Regel Milestone release-0.96: UI-Aenderungen weisen selbst nach) —
Tastaturpfad, Fokus nach dem Re-Display und ein axe-Smoke auf dem Formular im
Grenzfenster.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from ._axe import assert_within_baseline, load_baseline, run_axe
from ._date_helpers import date_field, expect_date_value, fill_date
from ._helpers import django_shell

pytestmark = [pytest.mark.e2e]

BASELINE_PATH = Path(__file__).with_name("a11y_baseline.json")

# Rueckfalltag Europe/Berlin 2026: 25.10., 03:00 -> 02:00 (letzter Sonntag im
# Oktober). Ein FESTES Datum, bewusst ohne jeden Bezug auf das aktuelle
# Kalenderdatum: die Zeitzonenregel dieses Tages ist unveraenderlich, waehrend
# "liegt in der Zukunft" eine Aussage ueber die Systemuhr waere — und damit
# genau keine Zusicherung. Dass die angelegten Ereignisse spaetere Tests
# derselben Session nicht stoeren, sichert stattdessen das Aufraeumen in
# ``_retire_events_of_the_double_hour``.
AMBIGUOUS_WALL_CLOCK = "2026-10-25T02:30"
SUMMER_UTC = "2026-10-25T00:30:00+00:00"
WINTER_UTC = "2026-10-25T01:30:00+00:00"

FOLD_SELECT = "#id_occurred_at_fold"
FOLD_ERROR = "#id_occurred_at_fold-error"
EVENT_DETAIL_URL = re.compile(r"/events/[0-9a-f-]+/$")

# Der axe-Schluessel dieser Seite steht NICHT in ``a11y_baseline.json`` — der
# Grenzfall darf gar keinen critical/serious-Verstoss erzeugen. Die Baseline
# wird nur abgebaut, nie erweitert.
AXE_PAGE_KEY = "event_create_fold_choice"


@pytest.fixture(autouse=True)
def _retire_events_of_the_double_hour(e2e_env):
    """Was dieser Test anlegt, nimmt er am Ende wieder aus dem Verkehr.

    Die E2E-Datenbank wird pro pytest-Session **einmal** geseedet; alles, was
    ein Test anlegt, sehen alle spaeteren Tests derselben Session. Ein Ereignis
    am Rueckfalltag hat in Zeitstrom-, Statistik- und Loeschfristen-Fenstern
    nichts verloren — und die frueher hier notierte Begruendung "das Datum
    liegt ja in der Zukunft" war eine Aussage ueber die Systemuhr, die ab dem
    26.10.2026 schlicht falsch waere.

    **Warum kein hartes DELETE:** Ereignisse lassen sich in dieser Anwendung
    grundsaetzlich nicht hart loeschen. Der Cascade traefe ``EventHistory``,
    und darauf liegt ein Datenbank-Trigger::

        ProgrammingError: EventHistory records are append-only — DELETE not allowed
        CONTEXT: PL/pgSQL function prevent_eventhistory_delete()

    Das ist eine gewollte Produktzusage (revisionssichere Historie), keine
    Huerde, die ein Test umgehen darf. Der Teardown setzt deshalb ``is_deleted``
    — dasselbe Flag, das auch der Loeschweg der Anwendung setzt und gegen das
    praktisch jede Ereignis-Abfrage filtert (``feed.py:171``,
    Modell-Kommentar ``event.py:120``): Zeitstrom, Statistik und Uebergabe sehen
    die Zeilen danach nicht mehr, eine Papierkorb-Ansicht fuer Ereignisse gibt
    es nicht.

    Gefiltert wird ueber die beiden EXAKTEN UTC-Zeitpunkte, die nur diese Datei
    erzeugen kann (die zwei Lesarten derselben Wanduhrzeit) — nicht ueber einen
    Tages- oder Bereichsfilter. Damit kann der Teardown nichts treffen, was er
    nicht selbst angelegt hat, auch wenn ein Test vorzeitig abbricht.
    """
    yield
    django_shell(
        e2e_env,
        "import datetime as dt;"
        " from core.models import Event;"
        f" instants = [dt.datetime.fromisoformat(s) for s in {[SUMMER_UTC, WINTER_UTC]!r}];"
        " qs = Event.objects.filter(occurred_at__in=instants, is_deleted=False);"
        " print('stillgelegt', qs.update(is_deleted=True))",
    )


def _provoke_the_question(page, base_url):
    """Formular oeffnen, Uhrzeit im Doppelfenster absenden, Rueckfrage abwarten.

    Der Dokumentationstyp wird bewusst explizit gewaehlt (nicht auf einen
    Facility-Default verlassen) — das ist das Muster aller uebrigen
    Ereignis-E2Es. Der Typwechsel loest einen HTMX-Swap des Feldblocks aus;
    gewartet wird auf dessen Response, nicht auf eine Uhr.
    """
    page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
    expect(page.locator("#main-content h1:has-text('Kontakt dokumentieren')")).to_be_visible(timeout=15000)
    # Vorbedingung des Tickets: ohne Grenzfall existiert das Feld nicht.
    expect(page.locator(FOLD_SELECT)).to_have_count(0)

    with page.expect_response(re.compile(r"/partials/events/fields/")):
        page.select_option("select[name='document_type']", label="Kontakt")
    fill_date(page, "#id_occurred_at", AMBIGUOUS_WALL_CLOCK)
    page.locator("#event-submit-btn").click()

    expect(page.locator(FOLD_SELECT)).to_be_visible(timeout=15000)
    expect(page.locator(FOLD_ERROR)).to_be_visible()
    # The select is visible as soon as the redisplay is parsed, before the
    # deferred date widget has replaced ``#id_occurred_at`` with its visible
    # clone. Wait for that observable state; acting earlier hits the native
    # input that the widget turns into a hidden transport a moment later.
    date_field(page, "#id_occurred_at")


def _stored_utc(e2e_env, url):
    """Den gespeicherten Zeitpunkt des gerade angelegten Ereignisses in UTC lesen.

    Ueber die ORM statt ueber die Detailseite: die Seite zeigt die Wanduhrzeit,
    und die ist fuer beide Lesarten dieselbe (02:30). Der Unterschied lebt
    ausschliesslich im gespeicherten Instant.
    """
    match = re.search(r"/events/([0-9a-f-]+)/$", url)
    assert match, f"Keine Ereignis-Detail-URL: {url}"
    script = (
        "import datetime as dt;"
        " from core.models import Event;"
        f" print(Event.objects.get(pk='{match.group(1)}').occurred_at.astimezone(dt.UTC).isoformat())"
    )
    return django_shell(e2e_env, script)[-1].strip()


class TestOccurrenceChoice:
    """Die zwei POSTs des Tickets — Rueckfrage, dann Speicherung."""

    def test_ambiguous_hour_asks_for_the_occurrence(self, authenticated_page, base_url):
        """Erster POST: kein 500er, kein stiller Verlust, sondern eine Rueckfrage."""
        page = authenticated_page
        _provoke_the_question(page, base_url)

        expect(page.locator(FOLD_ERROR)).to_contain_text("zweimal")
        expect(page.locator(FOLD_ERROR)).to_contain_text("Sommer- oder Winterzeit")
        # Die Uhrzeit selbst ist nicht falsch — sie ist nur noch nicht entschieden.
        expect(page.locator("#id_occurred_at-error")).to_have_count(0)
        expect_date_value(page, "#id_occurred_at", AMBIGUOUS_WALL_CLOCK)

    def test_the_question_takes_the_focus(self, authenticated_page, base_url):
        """WCAG 3.3.1/2.4.3: der Re-Display landet auf dem Feld, das die Frage stellt.

        Das Formular ist ein voller POST (kein ``hx-post``), das globale
        Fokus-Management aus ``focus-management.js`` haengt aber an
        ``htmx:afterSwap`` — hier greift also ``autofocus``, und zwar nur im
        Fehlerfall.
        """
        page = authenticated_page
        _provoke_the_question(page, base_url)

        # ``autofocus`` is applied by the browser asynchronously (on a later
        # rendering step), so the check waits for the focus instead of reading
        # ``document.activeElement`` at one arbitrary moment.
        expect(page.locator(FOLD_SELECT)).to_be_focused()

    def test_winter_time_choice_stores_the_second_hour(self, authenticated_page, base_url, e2e_env):
        page = authenticated_page
        _provoke_the_question(page, base_url)

        page.select_option(FOLD_SELECT, value="1")
        page.locator("#event-submit-btn").click()
        page.wait_for_url(EVENT_DETAIL_URL, timeout=15000)

        # Die Detailseite zeigt die Wanduhrzeit, nicht die Lesart.
        expect(page.locator("#main-content")).to_contain_text("02:30")
        assert _stored_utc(e2e_env, page.url) == WINTER_UTC

    def test_the_select_is_reachable_and_operable_by_keyboard(self, authenticated_page, base_url, e2e_env):
        """WCAG 2.1.1: erreichbar per Tab, bedienbar per Pfeiltaste — ohne Maus.

        Getabbt wird ab der Uhrzeit: ``<input type="datetime-local">`` ist in
        Chromium ein Verbund aus mehreren Segmenten (Tag/Monat/Jahr/Stunde/
        Minute), Tab wandert erst durch diese und verlaesst das Feld danach.
        Die Schleife ist deshalb gedeckelt statt auf einen einzigen Tastendruck
        gewettet; die beobachtete Fokusfolge steht in der Fehlermeldung.
        """
        page = authenticated_page
        _provoke_the_question(page, base_url)

        # Let the asynchronous ``autofocus`` land first; moving the focus
        # before that races with it. Then start from the visible time field.
        expect(page.locator(FOLD_SELECT)).to_be_focused()
        date_field(page, "#id_occurred_at").focus()
        expect(date_field(page, "#id_occurred_at")).to_be_focused()
        seen: list[str | None] = []
        for _ in range(10):
            page.keyboard.press("Tab")
            seen.append(page.evaluate("() => document.activeElement && document.activeElement.id"))
            if seen[-1] == "id_occurred_at_fold":
                break
        assert seen[-1] == "id_occurred_at_fold", f"Select per Tab nicht erreicht, Fokusfolge: {seen}"

        # Erste Pfeiltaste ab dem Leerwert: Sommerzeit (erste Stunde).
        page.keyboard.press("ArrowDown")
        expect(page.locator(FOLD_SELECT)).to_have_value("0")

        page.locator("#event-submit-btn").click()
        page.wait_for_url(EVENT_DETAIL_URL, timeout=15000)
        assert _stored_utc(e2e_env, page.url) == SUMMER_UTC


class TestOccurrenceChoiceAccessibility:
    """axe-Smoke auf dem Formular im Grenzfenster (Regel release-0.96)."""

    @pytest.mark.a11y
    def test_the_double_hour_form_has_no_blocking_axe_violations(self, authenticated_page, base_url):
        page = authenticated_page
        _provoke_the_question(page, base_url)

        baseline = load_baseline(BASELINE_PATH)
        assert AXE_PAGE_KEY not in baseline, (
            "Der Grenzfall bekommt keinen Bestandsschutz — die Baseline wird abgebaut, nicht erweitert."
        )
        result = run_axe(page, base_url)
        assert_within_baseline(AXE_PAGE_KEY, result, baseline)
