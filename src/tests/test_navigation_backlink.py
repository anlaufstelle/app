"""Orientierungs-Guards: der Rücklink jeder Objektseite (Refs #1802, Konzept #1801).

Das Subkonzept #1802 legt fest, dass **jede Objektseite mit genau einem
Rücklink beginnt** — nie mit einer Kette (R1), immer zur *Heimat* statt zur
Herkunft (R2), nie als Ersatz für ``?next=`` (R3). Vor diesem Guard existierten
fünf Pfeil-Dialekte an drei Positionen; die Zuordnung „welche Seite gehört zu
welcher Liste" lag mehrfach und gedriftet in den Templates.

Die Soll-Quelle ist ``core.nav``: die Aktiv-State-Familien **sind** die
Heimat-Tabelle. Dieser Guard prüft drei Ebenen:

1. *Datenebene* — jede erreichbare Route ist klassifiziert (Bereichs-Rücklink,
   ``BACKLINK_EXEMPT`` oder ``VIEW_PROVIDED_BACKLINK``). Kein stiller Fallback.
2. *Renderebene* — jede gerenderte Seite trägt den Marker
   ``data-testid="back-link"`` genau einmal (bzw. gar nicht, wenn ausgenommen)
   und **vor** ihrer ``<h1>``.
3. *Titelkopplung* — der Fenstertitel **ist** der H1-Text plus „– Anlaufstelle"
   (Konzept 3.9, Maintainer-Entscheid 5). Gleichheit, nicht Präfix: sonst
   ginge genau die Umformulierung durch, gegen die die Regel geschrieben ist.
   Abweichungen stehen begründet in ``TITLE_H1_EXCEPTIONS``.

Bewusst abgeleitet statt gepflegt: Seiten und Rollen kommen aus
``_authz_expectations.py``, die Heimat aus ``core.nav`` — eine neue Seite fällt
hier auf, nicht erst im Handtest.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

from core import nav
from core.services.client import request_client_deletion
from tests._authz_expectations import EXPECTATIONS, ROLES
from tests._rbac_helpers import resolve_fixture_kwargs

# Fixtures des AuthZ-Template-Guards mitbenutzen: dieselben fünf Akteure und
# derselbe Datenbestand, damit Listen und Detailseiten nicht leer rendern.
from tests.test_authz_template_links import guard_content, guard_users  # noqa: F401

# Kategorien ohne gerendertes Anwendungsdokument. ``public`` enthält neben
# Login-/Fehlerseiten die drei vom Service Worker vorgecachten Offline-Shells;
# deren Rücklink ist ein eigener Fall (Konzept 4.2: statisch und
# rollenagnostisch) und bewusst NICHT Teil dieses Laufs — nachgezogen in
# https://github.com/anlaufstelle/app/issues/1844.
NON_APP_CATEGORIES = frozenset({"export", "offline-api", "partials-htmx", "public"})

BACK_LINK_MARKER = 'data-testid="back-link"'

_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

TITLE_SUFFIX = "– Anlaufstelle"

# Benannte Ausnahmen von der Titel-Gleichheit (Konzept 3.9). Wert = Begründung;
# ``test_title_exceptions_are_reachable_and_reasoned`` erzwingt beides — einen
# nicht-leeren Grund und eine Seite, die es noch gibt.
TITLE_H1_EXCEPTIONS = {
    "core:event_detail": (
        "Die H1 ist der Name des Dokumentationstyps und damit über alle Ereignisse "
        "einer Person hinweg derselbe Text; mehrere gleichnamige Ereignis-Tabs wären "
        "ohne den Zeitpunkt im Titel ununterscheidbar. Das ist genau die "
        "Unterscheidbarkeit, für die Maintainer-Entscheid 5 den Bereichsnamen aus dem "
        "Titel heraushält — hier trägt sie der Zeitpunkt, der auf der Seite ohnehin als "
        "Untertitel direkt unter der H1 steht."
    ),
}


def _text(html_fragment: str) -> str:
    """Sichtbarer Text eines Fragments — Tags raus, Weißraum normalisiert."""
    return " ".join(_TAG_RE.sub(" ", html_fragment).split())


def _non_page_routes() -> set[str]:
    """Routen ohne Anwendungsdokument (Downloads, HTMX-Fragmente, POST-Handler)."""
    return set(nav.BACKLINK_NON_PAGE) | set(nav.KNOWN_NON_NAV_ROUTES)


def _app_pages():
    """Die Erwartungs-Einträge, die eine echte Anwendungsseite per GET liefern."""
    non_page = _non_page_routes()
    for exp in EXPECTATIONS:
        if exp.category in NON_APP_CATEGORIES:
            continue
        if dict(exp.methods).get("GET") is None:
            continue
        if exp.url_name.split(":")[-1] in non_page:
            continue
        yield exp


def _short(url_name: str) -> str:
    """``url_name`` ohne Namespace — die Schreibweise der Nav-Familien."""
    return url_name.split(":")[-1]


class TestBacklinkRegistry:
    """Datenebene: die Klassifikation ist vollständig, widerspruchsfrei, benannt."""

    def test_every_named_core_route_is_classified(self):
        """Vollständigkeits-Gate — kein stiller Fallback (Konzept 3.3, Schritt 4).

        Jede erreichbare Anwendungsseite ist entweder ausgenommen
        (``BACKLINK_EXEMPT``), bekommt ihren Rücklink aus dem Seitenkontext
        (``VIEW_PROVIDED_BACKLINK``, Objekt-Eltern nach Konzept 3.4), liefert
        einen Bereichs-Rücklink aus der Registry — oder ist als Nicht-Seite
        begründet ausgenommen. Genau diese Vollständigkeit verhindert die in
        Konzept 1.3 belegte Drift.
        """
        unclassified = sorted(
            exp.url_name
            for exp in _app_pages()
            if _short(exp.url_name) not in nav.BACKLINK_EXEMPT
            and _short(exp.url_name) not in nav.VIEW_PROVIDED_BACKLINK
            and nav.back_link_for(_short(exp.url_name)) is None
        )
        assert not unclassified, (
            f"Seiten ohne Rücklink-Klassifikation: {unclassified}. Entweder in die passende "
            "Nav-Familie in src/core/nav.py aufnehmen (Bereichs-Rücklink), in "
            "nav.BACKLINK_EXEMPT (Hub/Reiter/Liste/Assistent) oder mit Begründung in "
            "nav.VIEW_PROVIDED_BACKLINK bzw. nav.BACKLINK_NON_PAGE eintragen."
        )

    def test_exempt_pages_do_not_hide_a_different_home(self):
        """Eine Ausnahme verschweigt keine **fremde** Heimat.

        Eine Liste ist ihre eigene Heimat — dort zeigte ein Rücklink auf die
        Seite selbst, und die Ausnahme sagt nichts aus. Zeigt die Nav-Familie
        dagegen woanders hin, ist die Ausnahme eine echte Entscheidung und
        muss hier benannt sein. Die drei Schritte des Import-Assistenten sind
        der einzige Fall (Regel R7: Stepper statt Rücklink); jede weitere
        stille Ausnahme dieser Art bricht den Test.
        """
        fremd = {}
        for name in nav.BACKLINK_EXEMPT:
            link = nav._family_back_link(name)
            if link and _short(link.url_name) != name:
                fremd[name] = link.url_name
        assert fremd == {
            "import_create": "core:import_list",
            "import_mapping": "core:import_list",
            "import_preview": "core:import_list",
        }, f"Ausnahmen mit fremder Heimat: {fremd} — begründen (R7) oder Ausnahme entfernen."

    def test_exempt_and_view_provided_are_disjoint(self):
        overlap = sorted(set(nav.BACKLINK_EXEMPT) & set(nav.VIEW_PROVIDED_BACKLINK))
        assert not overlap, f"Routen in BACKLINK_EXEMPT UND VIEW_PROVIDED_BACKLINK: {overlap}"

    def test_backlink_label_equals_sidebar_label(self):
        """R2: der Rücklink heißt exakt wie der Navigations-Eintrag seiner Heimat.

        Das ist der Grund, warum die Labels nicht im Template stehen: „Zurück
        zur Inbox" vs. Nav-Eintrag „Aufgaben" und „Berichtigungsanträge" vs.
        „Berichtigungen" waren beide real (Konzept 1.1).
        """
        erwartet = {
            "client_detail": (nav.PERSONEN.label, "core:client_list"),
            "case_detail": (nav.FAELLE.label, "core:case_list"),
            "event_detail": (nav.ZEITSTROM.label, "core:zeitstrom"),
            "workitem_detail": (nav.AUFGABEN.label, "core:workitem_inbox"),
            "audit_detail": (nav.AUDIT_LOG.label, "core:audit_log"),
            "rectification_review": (nav.BERICHTIGUNGEN.label, "core:rectification_list"),
            "deletion_review": (nav.LOESCHANTRAEGE.label, "core:deletion_request_list"),
            "import_detail": (nav.DATENIMPORT.label, "core:import_list"),
            "config_doctype_detail": (nav.KONFIGURATION.label, "core:config_doctype_list"),
        }
        gefunden = {name: nav.back_link_for(name) for name in erwartet}
        assert {name: (link.label, link.url_name) for name, link in gefunden.items() if link} == erwartet

    def test_object_pages_resolve_to_the_tab_not_to_the_hub(self):
        """Konzept 3.6: unter einem Hub zeigt der Rücklink auf den **Reiter**.

        ``system_audit_detail`` gehört zum Reiter „Audit-Log", nicht zum
        Sidebar-Anker „Systembereich" — sonst spränge der Rücklink über die
        Reiterebene hinweg auf die Bereichs-Übersicht.
        """
        link = nav.back_link_for("system_audit_detail")
        assert link is not None
        assert link.url_name == "core:system_audit_list"

    def test_view_provided_entries_have_a_reason(self):
        for name, reason in nav.VIEW_PROVIDED_BACKLINK.items():
            assert reason.strip(), f"nav.VIEW_PROVIDED_BACKLINK[{name!r}] braucht eine Begründung."

    def test_non_page_entries_have_a_reason(self):
        for name, reason in nav.BACKLINK_NON_PAGE.items():
            assert reason.strip(), f"nav.BACKLINK_NON_PAGE[{name!r}] braucht eine Begründung."

    def test_classification_sets_reference_real_routes(self):
        """Bit-Rot-Schutz: jede Klassifikation zeigt auf eine deklarierte Route."""
        bekannt = {_short(exp.url_name) for exp in EXPECTATIONS}
        stray = sorted(
            name
            for name in (*nav.BACKLINK_EXEMPT, *nav.VIEW_PROVIDED_BACKLINK, *nav.BACKLINK_NON_PAGE)
            if name not in bekannt
        )
        assert not stray, f"Rücklink-Klassifikation für unbekannte Routen: {stray}"

    def test_back_link_for_is_none_for_exempt_and_view_provided(self):
        """Die Registry beantwortet nur die Bereichsfrage — nie die Objektfrage."""
        assert nav.back_link_for("client_list") is None
        assert nav.back_link_for("episode_create") is None


@pytest.fixture
def rendered_pages(client, request, guard_users, guard_content):  # noqa: F811
    """``(url_name, html)`` jeder erreichbaren Anwendungsseite — einmal gerendert.

    Je Seite genau **eine** Rolle: der Rücklink ist per Konzept 4.2 nie
    rollenabhängig (in allen Objekt-Eltern- und allen Bereichsfällen ist das
    Ziel gleich oder weiter gegated als die Seite). Das spart gegenüber der
    5×-Matrix des AuthZ-Guards rund 80 % der Requests; die Rollen-Achse prüft
    ohnehin ``test_authz_template_links``.
    """
    seiten: list[tuple[str, str]] = []
    nach_rolle: dict[str, list] = {}
    for exp in _app_pages():
        erlaubt = dict(exp.methods).get("GET") or frozenset()
        rolle = next((r for r in ROLES if r in erlaubt), None)
        if rolle:
            nach_rolle.setdefault(rolle, []).append(exp)
    assert nach_rolle, "Keine Seiten zu prüfen — Guard blind."

    for rolle, erwartungen in nach_rolle.items():
        client.force_login(guard_users[rolle])
        for exp in erwartungen:
            url = reverse(exp.url_name, kwargs=resolve_fixture_kwargs(exp.url_kwargs, request) or None)
            response = client.get(url)
            if response.status_code != 200 or "text/html" not in response.headers.get("Content-Type", ""):
                continue
            seiten.append((exp.url_name, response.content.decode()))
    assert seiten, "Keine Seite hat 200/HTML geliefert — Guard blind."
    return seiten


@pytest.mark.django_db
class TestBacklinkRendering:
    """Renderebene: Anzahl und Position des Markers auf der echten Seite."""

    def test_every_page_has_exactly_one_or_zero_back_links(self, rendered_pages):
        """R1: nie eine Kette, nie zwei Rückwege, nie gar keiner.

        „Zweimal dasselbe Ziel" war real (``events/edit.html``: „Abbrechen"
        und „Zurück"), „gar keiner" ebenfalls (beide ``deletion_review``-Seiten
        hatten keinerlei Ausweg außer den Formular-Schaltflächen).
        """
        befunde = []
        for url_name, html in rendered_pages:
            anzahl = html.count(BACK_LINK_MARKER)
            soll = 0 if _short(url_name) in nav.BACKLINK_EXEMPT else 1
            if anzahl != soll:
                befunde.append(f"{url_name}: {anzahl} Rücklinke, erwartet {soll}")
        assert not befunde, "Rücklink-Anzahl weicht ab (Konzept #1802, R1/R5):\n  " + "\n  ".join(befunde)

    def test_no_template_passes_a_second_crumb(self):
        """R1 statisch: kein Aufruf reicht dem Rücklink einen zweiten Krümel.

        ``{% back_link current=case.title %}`` rendert „← Fälle / Wohnungs-
        verlust" — eine zweite Ebene, deren Text zwei Zeilen tiefer als H1
        noch einmal steht. Der Renderguard oben zählt nur ``back-link``-Marker
        und sähe die Kette nicht; deshalb hier die Textprüfung. Der
        Inclusion-Tag kennt den Parameter seit #1802 nicht mehr — ein Aufruf
        bräche also ohnehin, aber erst zur Renderzeit der betroffenen Seite.
        """
        wurzel = Path(settings.BASE_DIR) / "templates"
        aufrufe = re.compile(r"\{%\s*back_link\b[^%]*?\bcurrent\s*=")
        dateien = sorted(wurzel.rglob("*.html"))
        assert dateien, f"Keine Templates unter {wurzel} — Guard blind."
        treffer = sorted(
            str(pfad.relative_to(wurzel)) for pfad in dateien if aufrufe.search(pfad.read_text(encoding="utf-8"))
        )
        assert not treffer, (
            "back_link mit current= aufgerufen — R1 verbietet die Kette, der Objektname "
            f"steht bereits in der H1 direkt darunter: {treffer}"
        )

    def test_back_link_stands_before_the_first_h1(self, rendered_pages):
        """Position: Zeile 1 des Inhalts, nicht Fußzeile und nicht rechts oben.

        Fängt die acht Fußzeilen-/Rechtsbündig-Fälle aus Konzept 1.1 — ein
        Rücklink unter 60 Zeilen Detailtabelle orientiert niemanden.
        """
        befunde = []
        for url_name, html in rendered_pages:
            marker = html.find(BACK_LINK_MARKER)
            if marker == -1:
                continue
            h1 = _H1_RE.search(html)
            if h1 and h1.start() < marker:
                befunde.append(f"{url_name}: Rücklink steht hinter der H1")
        assert not befunde, "Rücklink nicht am Anfang des Inhalts (Konzept 3.1):\n  " + "\n  ".join(befunde)


@pytest.mark.django_db
class TestNoSecondWayHome:
    """R1 in Seitenzuständen, die die Routenmatrix nicht erreicht.

    ``rendered_pages`` fährt jede Route genau einmal im Normalzustand. Der
    Löschantrag mit **bereits laufendem** Antrag ist ein Zweig dahinter — und
    dort stand neben dem Rücklink oben ein zweiter Button „Zurück zur Person"
    auf dasselbe Ziel. Genau der Doppelbefund aus Konzept 1.1 („zweimal
    dasselbe Ziel", ``events/edit.html``), nur eine Verzweigung tiefer.
    """

    def test_pending_delete_request_offers_exactly_one_way_back(self, client, staff_user, client_identified):
        request_client_deletion(client_identified, staff_user, "Erster Grund")
        client.force_login(staff_user)
        url = reverse("core:client_delete_request", args=[client_identified.pk])
        html = client.get(url).content.decode()

        assert 'data-testid="delete-request-already-pending"' in html, "Zweig nicht erreicht"
        ziel = reverse("core:client_detail", args=[client_identified.pk])
        wege = html.count(f'href="{ziel}"')
        assert wege == 1, f"{wege} Wege zurück zur Person — R1 verlangt genau einen, und das ist der Rücklink oben."


@pytest.mark.django_db
class TestTitleH1Coupling:
    """Konzept 3.9: Fenstertitel und Orientierungszeile sagen dasselbe.

    Der Fenstertitel ist der **Objektname**, die Orientierungszeile der
    Bereich — zusammen ergeben sie den Kontext, einzeln keine Redundanz. Der
    Bereich kommt bewusst NICHT in den Titel (Maintainer-Entscheid 5): bei
    20–25 sichtbaren Zeichen im Tab verdrängt er genau das, was unterscheidet.
    """

    def test_titles_end_with_the_product_name(self, rendered_pages):
        befunde = [
            f"{url_name}: {_text(treffer.group(1))!r}"
            for url_name, html in rendered_pages
            if (treffer := _TITLE_RE.search(html)) and not _text(treffer.group(1)).endswith(TITLE_SUFFIX)
        ]
        assert not befunde, f"Fenstertitel ohne {TITLE_SUFFIX!r}:\n  " + "\n  ".join(befunde)

    def test_object_page_titles_equal_their_h1_plus_suffix(self, rendered_pages):
        """Auf Objektseiten ist der Titel der H1-Text — nicht eine zweite Formulierung.

        Konzept 3.9 formuliert die Nebenregel als Gleichheit: „H1-Text und
        ``{% block title %}``-Text müssen identisch sein (bis auf den Suffix)".
        Ein ``startswith`` ließe genau die Drift durch, gegen die die Regel
        geschrieben ist — „Löschantrag prüfen" vs. „Löschantrag prüfen für
        A-4471" beginnen gleich und sagen Verschiedenes.

        Ausnahmen stehen benannt und begründet in ``TITLE_H1_EXCEPTIONS``;
        sie bleiben trotzdem an ``startswith`` + Suffix gebunden — eine
        Ausnahme darf den Titel *erweitern*, nicht umformulieren.
        """
        befunde = []
        for url_name, html in rendered_pages:
            if _short(url_name) in nav.BACKLINK_EXEMPT:
                continue
            titel, h1 = _TITLE_RE.search(html), _H1_RE.search(html)
            if not titel or not h1:
                continue
            titel_text, h1_text = _text(titel.group(1)), _text(h1.group(1))
            if not h1_text:
                continue
            if url_name in TITLE_H1_EXCEPTIONS:
                if not (titel_text.startswith(h1_text) and titel_text.endswith(TITLE_SUFFIX)):
                    befunde.append(
                        f"{url_name}: Ausnahme erlaubt einen Zusatz, keine Umformulierung — "
                        f"Titel {titel_text!r}, H1 {h1_text!r}"
                    )
                continue
            if titel_text != f"{h1_text} {TITLE_SUFFIX}":
                befunde.append(f"{url_name}: Titel {titel_text!r} ist nicht H1 {h1_text!r} + {TITLE_SUFFIX!r}")
        assert not befunde, (
            "Fenstertitel und H1 driften (Konzept 3.9):\n  "
            + "\n  ".join(befunde)
            + "\n  Entweder den Titel auf die H1 normieren oder mit Begründung in "
            "TITLE_H1_EXCEPTIONS aufnehmen."
        )

    def test_title_exceptions_are_reachable_and_reasoned(self, rendered_pages):
        """Bit-Rot-Schutz: jede Ausnahme zeigt auf eine gerenderte Seite und nennt einen Grund.

        Ohne diesen Test wäre die Ausnahmeliste der bequeme Ort, an dem eine
        normierte Seite unbemerkt stehen bliebe — und der nächste Titel
        einfach mit eingetragen würde.
        """
        gerendert = {url_name for url_name, _ in rendered_pages}
        unerreichbar = sorted(set(TITLE_H1_EXCEPTIONS) - gerendert)
        assert not unerreichbar, (
            f"TITLE_H1_EXCEPTIONS für nicht gerenderte Seiten: {unerreichbar} — "
            "Eintrag entfernen oder Fixture ergänzen."
        )
        for url_name, grund in TITLE_H1_EXCEPTIONS.items():
            assert grund.strip(), f"TITLE_H1_EXCEPTIONS[{url_name!r}] braucht eine Begründung."
