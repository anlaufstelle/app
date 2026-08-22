"""E2E: Reichweite der Zugriffs-Protokollierung an der Kontaktstufe (Refs #1862).

Die Oberflaeche sagte an zwei Stellen „jeder Zugriff wird protokolliert" —
an der Legendenzeile der Personenlisten und im ``title`` des Kontaktstufen-
Merkmals. Das ist falsch: ``AuditLog.Action.VIEW_QUALIFIED`` entsteht
projektweit an genau einer Stelle, im GET der Personen-Detailseite
(``core/views/clients.py``). Das **Bearbeiten-Formular** derselben Person
zeigt ihre Zusatzangaben entschluesselt im Klartext und schreibt nichts.

Dieser Lauf haelt beide Haelften am laufenden System fest: die praezisierte
Zusage in der ausgelieferten Oberflaeche **und** den Gegenfall, der sie
noetig gemacht hat.
"""

import subprocess
import uuid

import pytest

from tests.e2e._helpers import python_bin as _python
from tests.e2e._selectors import find_client_link

pytestmark = pytest.mark.e2e

#: Die pauschale Zusage, die nirgends mehr stehen darf.
BLANKET_CLAIM = "jeder Zugriff wird protokolliert"
#: Die praezisierten Fassungen (Legende bzw. Badge-``title``).
SCOPED_LEGEND_CLAIM = "das Öffnen der Detailseite wird protokolliert"
SCOPED_BADGE_CLAIM = "das Öffnen der Detailseite dieser Person wird protokolliert"


def _shell(e2e_env, script):
    out = subprocess.run(
        [_python(), "src/manage.py", "shell", "--no-imports", "-c", script],
        capture_output=True,
        text=True,
        env=e2e_env,
        check=True,
    )
    return [ln for ln in out.stdout.strip().splitlines() if ln.strip()]


def _seed_qualified_client(e2e_env):
    """Frische Person der Stufe „Mit Zusatzangaben" — unabhaengig vom Seed-Katalog.

    Gleiches Muster wie ``test_client_export._seed_qualified_client`` (Refs
    #1765): Der Test darf nicht davon abhaengen, dass irgendeine Bestandsperson
    zufaellig ``qualified`` ist.
    """
    pseudonym = f"E2E-Protokollreichweite-{uuid.uuid4().hex[:8]}"
    script = (
        "from core.models import Client, Facility;"
        " from core.models.user import User;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='emma');"
        f" Client.objects.create(facility=f, contact_stage='qualified', pseudonym='{pseudonym}', created_by=u)"
    )
    _shell(e2e_env, script)
    return pseudonym


def _view_qualified_count(e2e_env, pseudonym):
    """Anzahl der ``VIEW_QUALIFIED``-Zeilen zu genau dieser Person."""
    script = (
        "from core.models import AuditLog, Client;"
        f" c = Client.objects.get(pseudonym='{pseudonym}');"
        " print(AuditLog.objects.filter(action=AuditLog.Action.VIEW_QUALIFIED, target_id=str(c.pk)).count())"
    )
    return int(_shell(e2e_env, script)[-1])


class TestContactStageClaimIsScoped:
    """Die ausgelieferte Oberflaeche nennt die eine Flaeche, die den Eintrag ausloest."""

    def test_client_list_legend_names_only_the_detail_page(self, staff_page, base_url):
        page = staff_page
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")
        legend = page.locator("[data-testid='contact-stage-legend']").first
        legend.wait_for(state="attached", timeout=30000)
        text = legend.inner_text()
        assert BLANKET_CLAIM not in text, f"Legende sagt weiterhin die pauschale Zusage zu: {text!r}"
        assert SCOPED_LEGEND_CLAIM in text, text

    def test_qualified_badge_title_names_only_the_detail_page(self, staff_page, base_url, e2e_env):
        pseudonym = _seed_qualified_client(e2e_env)
        page = staff_page
        page.goto(f"{base_url}/clients/?q={pseudonym}")
        page.wait_for_load_state("domcontentloaded")
        row = page.locator(f"[data-testid='client-row'][data-pseudonym='{pseudonym}']")
        row.wait_for(state="attached", timeout=30000)
        title = row.locator("[data-testid='client-stage-badge']").first.get_attribute("title")
        assert title is not None, "Kontaktstufen-Merkmal ohne erklaerenden ``title``"
        assert BLANKET_CLAIM not in title, f"Badge sagt weiterhin die pauschale Zusage zu: {title!r}"
        assert SCOPED_BADGE_CLAIM in title, title


class TestOnlyTheDetailPageWritesViewQualified:
    """Der Gegenfall, der die Praezisierung noetig gemacht hat.

    Das Bearbeiten-Formular zeigt dieselben Zusatzangaben im Klartext und
    hinterlaesst **keine** Spur; erst die Detailseite schreibt
    ``VIEW_QUALIFIED``.
    """

    def test_edit_form_leaves_no_trace_but_detail_page_does(self, staff_page, base_url, e2e_env):
        pseudonym = _seed_qualified_client(e2e_env)
        page = staff_page

        page.goto(f"{base_url}/clients/?q={pseudonym}")
        page.wait_for_load_state("domcontentloaded")
        find_client_link(page, pseudonym).wait_for(state="attached", timeout=30000)
        client_url = find_client_link(page, pseudonym).get_attribute("href")
        assert client_url, "Kein Detail-Link zur angelegten Person gefunden"

        before = _view_qualified_count(e2e_env, pseudonym)

        # 1. Bearbeiten-Formular: zeigt Klartext, schreibt nichts.
        page.goto(f"{base_url}{client_url}edit/")
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("input#id_pseudonym").input_value() == pseudonym
        assert _view_qualified_count(e2e_env, pseudonym) == before, (
            "Das Bearbeiten-Formular schreibt jetzt VIEW_QUALIFIED — die auf die "
            "Detailseite eingegrenzte Zusage in Legende, Badge, help_text, "
            "Handbuch, Glossar und Fachkonzept ist damit zu eng geworden."
        )

        # 2. Detailseite: schreibt genau einen Eintrag.
        page.goto(f"{base_url}{client_url}")
        page.wait_for_load_state("domcontentloaded")
        assert _view_qualified_count(e2e_env, pseudonym) == before + 1, (
            "Der Aufruf der Personen-Detailseite schreibt keinen VIEW_QUALIFIED-"
            "Eintrag mehr — dann ist die Zusage in der Oberflaeche gegenstandslos."
        )
