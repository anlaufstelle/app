"""E2E: flex-wrap-Fallback in Prüf- und Bestätigungsformularen (Refs #2356, #2375).

Herkunft: Während der Umsetzung von #2332 fiel derselbe Randfall
(fehlendes ``flex-wrap`` an einem ``flex items-center space-x-*``-Formular)
in zwei weiteren Genehmigungsformularen auf:

- ``src/templates/core/clients/merge_review.html:111``
  (Zusammenführungs-Antrag)
- ``src/templates/core/cases/deletion_review.html:76``
  (Fall-Löschantrag)

Beide Formulare tragen nur zwei Knöpfe ("Genehmigen"/"Ablehnen") ohne
``flex-wrap`` — bei schmaler Kartenbreite können sie über den Kartenrand
hinauslaufen statt in eine zweite Zeile zu wandern, analog zum in #2332
gefundenen Art.-18-Knopf. Muster (Seed, Bounding-Box-Assertion, axe-Smoke)
aus ``test_client_detail_mobile_art18.py`` übernommen.

Messbefund vor dem Fix: mit den aktuellen kurzen Beschriftungen
("Genehmigen"/"Ablehnen") passt die Zeile bei 375 px noch ohne Überlauf —
der Befund ist laut Ticket ausdrücklich präventiv ("kein aktueller
Nutzerbefund"). Der Bounding-Box-Nachweis simuliert deshalb genau das im
Ticket genannte Auslöseszenario "z. B. lange Übersetzungen": die
Knopf-Beschriftungen werden vor der Messung per ``page.evaluate`` durch
ungebrochene (leerzeichenfreie) Wörter ersetzt, wie sie ein langes
zusammengesetztes deutsches Substantiv oder eine andere Sprache liefern
könnten. Das erzwingt eine Knopf-Mindestbreite, die ohne ``flex-wrap``
über die verfügbare Zeilenbreite hinausschießt — ROT vor dem Fix, GRÜN
danach. axe- und Tastaturpfad-Nachweis laufen dagegen mit der echten
Produktionsbeschriftung.
"""

from __future__ import annotations

import re
import subprocess
import uuid

import pytest

from ._axe import AXE_TAGS, run_axe
from ._helpers import python_bin as _python

pytestmark = pytest.mark.e2e

_NARROW_WIDTH = 375
_NARROW_HEIGHT = 812
_DESKTOP_WIDTH = 1280
_DESKTOP_HEIGHT = 900


def _shell(e2e_env, script):
    out = subprocess.run(
        [_python(), "src/manage.py", "shell", "--no-imports", "-c", script],
        capture_output=True,
        text=True,
        env=e2e_env,
        check=True,
    )
    return [ln for ln in out.stdout.strip().splitlines() if ln.strip()]


_CLEANUP = """
from core.models import Case, Client, Event
from core.models.workitem import DeletionRequest
clients = Client.objects.filter(pseudonym__startswith="E2E-ReviewWrap-")
client_pks = [str(pk) for pk in clients.values_list("pk", flat=True)]
cases = Case.objects.filter(client__in=clients)
case_pks = [str(pk) for pk in cases.values_list("pk", flat=True)]
events = Event.objects.filter(client__in=clients)
event_pks = [str(pk) for pk in events.values_list("pk", flat=True)]
DeletionRequest.objects.filter(target_id__in=case_pks + client_pks + event_pks).delete()
DeletionRequest.objects.filter(merge_into__in=clients).delete()
events.delete()
cases.delete()
clients.update(merged_into=None)
clients.delete()
print("ok")
"""


@pytest.fixture(autouse=True)
def _review_forms_cleanup(e2e_env):
    """Räumt die Wegwerf-Personen/-Fälle dieser Datei ab (Muster test_client_merge.py)."""
    yield
    _shell(e2e_env, _CLEANUP)


def _seed_case_review(e2e_env, staff_page, base_url) -> str:
    """Fall-Löschantrag (Vier-Augen, ``contact_stage='qualified'``) — liefert den Falltitel."""
    marke = uuid.uuid4().hex[:8]
    pseudonym = f"E2E-ReviewWrap-Case-Person-{marke}"
    titel = f"E2E-ReviewWrap-Case-Fall-{marke}"
    script = (
        "from core.models import Case, Client, Facility;"
        " from core.models.user import User;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='emma');"
        f" c = Client.objects.create(facility=f, contact_stage='qualified',"
        f" pseudonym='{pseudonym}', created_by=u);"
        f" k = Case.objects.create(facility=f, client=c, title='{titel}',"
        " description='E2E-ReviewWrap', created_by=u);"
        " print(k.pk)"
    )
    case_pk = _shell(e2e_env, script)[-1].strip()

    staff_page.goto(f"{base_url}/cases/{case_pk}/delete/")
    staff_page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/delete/$"))
    staff_page.locator("textarea[name='reason']").fill("E2E: Formular-Umbruch-Nachweis")
    staff_page.locator("[data-testid='case-delete-submit']").click()
    staff_page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/$"))
    return titel


def _seed_merge_review(e2e_env, staff_page, base_url) -> str:
    """Zusammenführungs-Antrag (Vier-Augen, ``qualified`` → ``identified``) — liefert das Quelle-Pseudonym."""
    marke = uuid.uuid4().hex[:8]
    quelle = f"E2E-ReviewWrap-Merge-Quelle-{marke}"
    ziel = f"E2E-ReviewWrap-Merge-Ziel-{marke}"
    script = (
        "from core.models import Client, Facility;"
        " from core.models.user import User;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='emma');"
        f" s = Client.objects.create(facility=f, contact_stage='qualified',"
        f" pseudonym='{quelle}', created_by=u);"
        f" t = Client.objects.create(facility=f, contact_stage='identified',"
        f" pseudonym='{ziel}', created_by=u);"
        " print(s.pk); print(t.pk)"
    )
    lines = _shell(e2e_env, script)
    source_pk, target_pk = lines[-2].strip(), lines[-1].strip()

    staff_page.goto(f"{base_url}/clients/{source_pk}/merge/?target={target_pk}")
    staff_page.wait_for_load_state("domcontentloaded")
    staff_page.locator("[data-testid='merge-four-eyes-hint']").first.wait_for(state="visible", timeout=30000)
    staff_page.locator("textarea[name='reason']").fill("E2E: Formular-Umbruch-Nachweis")
    staff_page.locator("[data-testid='merge-request-submit']").click()
    staff_page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
    return quelle


def _seed_shared_deletion_review(e2e_env, target_type: str) -> str:
    """Legt einen Personen- oder Ereignis-Antrag für die gemeinsame Prüfvorlage an."""
    marke = uuid.uuid4().hex[:8]
    pseudonym = f"E2E-ReviewWrap-{target_type}-Person-{marke}"
    event_setup = ""
    target_expression = "c.pk"
    if target_type == "Event":
        event_setup = (
            " dt = DocumentType.objects.get(facility=f, name='Kontakt');"
            " e = Event.objects.create(facility=f, client=c, document_type=dt,"
            " occurred_at=timezone.now(), data_json={}, created_by=u);"
        )
        target_expression = "e.pk"
    script = (
        "from django.utils import timezone;"
        " from core.models import Client, DeletionRequest, DocumentType, Event, Facility;"
        " from core.models.user import User;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='miriam');"
        f" c = Client.objects.create(facility=f, contact_stage='qualified',"
        f" pseudonym='{pseudonym}', created_by=u);"
        f"{event_setup}"
        f" dr = DeletionRequest.objects.create(facility=f, target_type='{target_type}',"
        f" target_id={target_expression}, reason='E2E: Formular-Umbruch-Nachweis', requested_by=u);"
        " print(dr.pk)"
    )
    return _shell(e2e_env, script)[-1].strip()


def _seed_client_confirmation(e2e_env) -> str:
    """Legt eine qualifizierte Wegwerf-Person für die Löschbestätigung an."""
    marke = uuid.uuid4().hex[:8]
    pseudonym = f"E2E-ReviewWrap-Confirm-Person-{marke}"
    script = (
        "from core.models import Client, Facility;"
        " from core.models.user import User;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='miriam');"
        f" c = Client.objects.create(facility=f, contact_stage='qualified',"
        f" pseudonym='{pseudonym}', created_by=u);"
        " print(c.pk)"
    )
    return _shell(e2e_env, script)[-1].strip()


def _open_review_by_card_text(lead_page, base_url, text: str) -> None:
    """Antragskarte per Text finden (Titel bzw. Quelle-Pseudonym) und die Prüfseite öffnen."""
    lead_page.goto(f"{base_url}/deletion-requests/")
    lead_page.wait_for_load_state("domcontentloaded")
    karte = lead_page.locator(f"[data-testid='deletion-request-card']:has-text('{text}')").first
    karte.wait_for(state="visible", timeout=30000)
    karte.locator("[data-testid='deletion-review-link']").click()
    lead_page.wait_for_url(re.compile(r"/review/$"))


_LONG_APPROVE_LABEL = "Genehmigungserklaerung"
_LONG_REJECT_LABEL = "Ablehnungsbegruendung"


def _stretch_button_labels(page) -> None:
    """Simuliert das Ticket-Szenario "z. B. lange Übersetzungen": die
    Knopf-Beschriftungen werden durch ungebrochene (leerzeichenfreie) Wörter
    ersetzt, ohne Übersetzungsdateien anzufassen. Nur für den
    Bounding-Box-Nachweis — axe/Tastaturpfad laufen mit der echten
    Beschriftung."""
    page.evaluate(
        """([approveLabel, rejectLabel]) => {
            const approve = document.querySelector("form button[name='action'][value='approve']");
            const reject = document.querySelector("form button[name='action'][value='reject']");
            if (approve) approve.textContent = approveLabel;
            if (reject) reject.textContent = rejectLabel;
        }""",
        [_LONG_APPROVE_LABEL, _LONG_REJECT_LABEL],
    )


def _assert_buttons_fully_inside_viewport(page, width: int) -> None:
    approve = page.locator("form button[name='action'][value='approve']")
    reject = page.locator("form button[name='action'][value='reject']")
    approve.wait_for(state="visible", timeout=30000)

    for locator, label in ((approve, "Genehmigen"), (reject, "Ablehnen")):
        box = locator.bounding_box()
        assert box is not None, f"Knopf '{label}' nicht im DOM gefunden."
        assert box["x"] >= 0, f"Knopf '{label}' beginnt links ausserhalb des Sichtfensters (x={box['x']})."
        assert box["x"] + box["width"] <= width, (
            f"Knopf '{label}' liegt (teilweise) ausserhalb des {width}px-Sichtfensters: "
            f"x={box['x']}, width={box['width']}, x+width={box['x'] + box['width']}."
        )


def _assert_axe_clean(page, base_url) -> None:
    result = run_axe(page, base_url, tags=AXE_TAGS)
    blocking = result.blocking()
    assert not blocking, f"axe critical/serious auf {page.url} ({page.viewport_size}): {blocking}"


def _assert_tab_order_unchanged(page) -> None:
    """Tastaturpfad (WCAG 2.1.1): Genehmigen zuerst, dann Ablehnen — unveraendert."""
    approve = page.locator("form button[name='action'][value='approve']")
    approve.focus()
    assert page.evaluate("document.activeElement.value") == "approve"
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.value") == "reject"


def _assert_same_row_at_desktop(first, second) -> None:
    first_box = first.bounding_box()
    second_box = second.bounding_box()
    assert first_box is not None and second_box is not None
    assert abs(first_box["y"] - second_box["y"]) <= 2, (
        f"Bedienelemente stehen bei {_DESKTOP_WIDTH}px nicht mehr in einer Zeile: "
        f"first.y={first_box['y']}, second.y={second_box['y']}."
    )


def _assert_shared_review_surface(page, base_url) -> None:
    approve = page.locator("form button[name='action'][value='approve']")
    reject = page.locator("form button[name='action'][value='reject']")

    page.set_viewport_size({"width": _NARROW_WIDTH, "height": _NARROW_HEIGHT})
    page.reload()
    page.wait_for_load_state("domcontentloaded")
    _stretch_button_labels(page)
    _assert_buttons_fully_inside_viewport(page, _NARROW_WIDTH)

    page.reload()
    page.wait_for_load_state("domcontentloaded")
    _assert_axe_clean(page, base_url)
    _assert_tab_order_unchanged(page)

    page.set_viewport_size({"width": _DESKTOP_WIDTH, "height": _DESKTOP_HEIGHT})
    page.reload()
    page.wait_for_load_state("domcontentloaded")
    _assert_same_row_at_desktop(approve, reject)


def _assert_confirmation_surface(page, base_url) -> None:
    submit = page.locator("#main-content form button[type='submit']")
    cancel = page.locator("#main-content form a").last

    page.set_viewport_size({"width": _NARROW_WIDTH, "height": _NARROW_HEIGHT})
    page.reload()
    page.wait_for_load_state("domcontentloaded")
    page.evaluate(
        """() => {
            document.querySelector("#main-content form button[type='submit']").textContent =
                "Loeschantragverbindlichstellen";
            document.querySelector("#main-content form a").textContent =
                "VorgangohneAenderungabbrechen";
        }"""
    )
    for locator, label in ((submit, "Bestätigen"), (cancel, "Abbrechen")):
        box = locator.bounding_box()
        assert box is not None, f"Bedienelement '{label}' nicht im DOM gefunden."
        assert box["x"] >= 0
        assert box["x"] + box["width"] <= _NARROW_WIDTH, (
            f"Bedienelement '{label}' liegt ausserhalb des {_NARROW_WIDTH}px-Sichtfensters: "
            f"x={box['x']}, width={box['width']}, x+width={box['x'] + box['width']}."
        )

    page.reload()
    page.wait_for_load_state("domcontentloaded")
    _assert_axe_clean(page, base_url)
    submit.focus()
    assert page.evaluate("document.activeElement.tagName") == "BUTTON"
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.tagName") == "A"

    page.set_viewport_size({"width": _DESKTOP_WIDTH, "height": _DESKTOP_HEIGHT})
    page.reload()
    page.wait_for_load_state("domcontentloaded")
    _assert_same_row_at_desktop(submit, cancel)


class TestCaseDeletionReviewFormWrap:
    """``cases/deletion_review.html:76`` — Fall-Löschantrag."""

    def test_buttons_stay_inside_the_375px_viewport(self, staff_page, lead_page, base_url, e2e_env):
        titel = _seed_case_review(e2e_env, staff_page, base_url)
        _open_review_by_card_text(lead_page, base_url, titel)
        page = lead_page
        page.set_viewport_size({"width": _NARROW_WIDTH, "height": _NARROW_HEIGHT})
        page.reload()
        page.wait_for_load_state("domcontentloaded")
        _stretch_button_labels(page)

        _assert_buttons_fully_inside_viewport(page, _NARROW_WIDTH)

    def test_axe_smoke_stays_clean_at_375px(self, staff_page, lead_page, base_url, e2e_env):
        titel = _seed_case_review(e2e_env, staff_page, base_url)
        _open_review_by_card_text(lead_page, base_url, titel)
        page = lead_page
        page.set_viewport_size({"width": _NARROW_WIDTH, "height": _NARROW_HEIGHT})
        page.reload()
        page.wait_for_load_state("domcontentloaded")

        _assert_axe_clean(page, base_url)

    def test_keyboard_tab_order_and_desktop_layout_unchanged(self, staff_page, lead_page, base_url, e2e_env):
        titel = _seed_case_review(e2e_env, staff_page, base_url)
        _open_review_by_card_text(lead_page, base_url, titel)
        page = lead_page
        page.set_viewport_size({"width": _DESKTOP_WIDTH, "height": _DESKTOP_HEIGHT})
        page.reload()
        page.wait_for_load_state("domcontentloaded")

        _assert_tab_order_unchanged(page)

        # Desktop-Layout unveraendert: beide Knoepfe bleiben in einer Zeile.
        approve_box = page.locator("form button[name='action'][value='approve']").bounding_box()
        reject_box = page.locator("form button[name='action'][value='reject']").bounding_box()
        assert approve_box is not None and reject_box is not None
        assert abs(approve_box["y"] - reject_box["y"]) <= 2, (
            f"Knoepfe stehen bei {_DESKTOP_WIDTH}px nicht mehr in einer Zeile: "
            f"approve.y={approve_box['y']}, reject.y={reject_box['y']}."
        )


class TestMergeReviewFormWrap:
    """``clients/merge_review.html:111`` — Zusammenführungs-Antrag."""

    def test_buttons_stay_inside_the_375px_viewport(self, staff_page, lead_page, base_url, e2e_env):
        quelle = _seed_merge_review(e2e_env, staff_page, base_url)
        _open_review_by_card_text(lead_page, base_url, quelle)
        page = lead_page
        page.locator("[data-testid='merge-review-page']").wait_for(state="visible", timeout=30000)
        page.set_viewport_size({"width": _NARROW_WIDTH, "height": _NARROW_HEIGHT})
        page.reload()
        page.wait_for_load_state("domcontentloaded")
        _stretch_button_labels(page)

        _assert_buttons_fully_inside_viewport(page, _NARROW_WIDTH)

    def test_axe_smoke_stays_clean_at_375px(self, staff_page, lead_page, base_url, e2e_env):
        quelle = _seed_merge_review(e2e_env, staff_page, base_url)
        _open_review_by_card_text(lead_page, base_url, quelle)
        page = lead_page
        page.locator("[data-testid='merge-review-page']").wait_for(state="visible", timeout=30000)
        page.set_viewport_size({"width": _NARROW_WIDTH, "height": _NARROW_HEIGHT})
        page.reload()
        page.wait_for_load_state("domcontentloaded")

        _assert_axe_clean(page, base_url)

    def test_keyboard_tab_order_and_desktop_layout_unchanged(self, staff_page, lead_page, base_url, e2e_env):
        quelle = _seed_merge_review(e2e_env, staff_page, base_url)
        _open_review_by_card_text(lead_page, base_url, quelle)
        page = lead_page
        page.locator("[data-testid='merge-review-page']").wait_for(state="visible", timeout=30000)
        page.set_viewport_size({"width": _DESKTOP_WIDTH, "height": _DESKTOP_HEIGHT})
        page.reload()
        page.wait_for_load_state("domcontentloaded")

        _assert_tab_order_unchanged(page)

        approve_box = page.locator("form button[name='action'][value='approve']").bounding_box()
        reject_box = page.locator("form button[name='action'][value='reject']").bounding_box()
        assert approve_box is not None and reject_box is not None
        assert abs(approve_box["y"] - reject_box["y"]) <= 2, (
            f"Knoepfe stehen bei {_DESKTOP_WIDTH}px nicht mehr in einer Zeile: "
            f"approve.y={approve_box['y']}, reject.y={reject_box['y']}."
        )


class TestSharedDeletionReviewFormWrap:
    """Gemeinsame Partial für Personen- und Ereignis-Prüfseiten (Refs #2375)."""

    @pytest.mark.parametrize("target_type", ["Client", "Event"])
    def test_shared_review_actions_wrap_without_changing_accessibility(self, target_type, lead_page, base_url, e2e_env):
        request_pk = _seed_shared_deletion_review(e2e_env, target_type)
        page = lead_page
        page.goto(f"{base_url}/deletion-requests/{request_pk}/review/")
        page.wait_for_load_state("domcontentloaded")

        _assert_shared_review_surface(page, base_url)


class TestClientDeletionConfirmationWrap:
    """Personen-Löschbestätigung mit Bestätigen/Abbrechen (Refs #2375)."""

    def test_confirmation_actions_wrap_without_changing_accessibility(self, staff_page, base_url, e2e_env):
        client_pk = _seed_client_confirmation(e2e_env)
        page = staff_page
        page.goto(f"{base_url}/clients/{client_pk}/delete/")
        page.wait_for_load_state("domcontentloaded")

        _assert_confirmation_surface(page, base_url)
