"""E2E: Fall-Löschweg — Antrag, Bestätigung, Papierkorb, Wiederherstellung.

Refs #1818,
[ADR-044](../../../docs/adr/044-fall-loeschpfad.md). Gegenstück zu
``test_client_deletion_workflow.py`` auf der Fall-Achse; die Klickstrecken
folgen den Prüfsätzen unter ``## [Unreleased]`` in ``docs/dev/hand-checks.md``.

Die Weiche des Löschwegs hängt an der **Kontaktstufe der Person** (P1c) — der
Test darf sich also nicht darauf verlassen, dass irgendeine Seed-Person zufällig
auf der gesuchten Stufe steht. Jeder Test legt sich Person **und** Fall über
``manage.py shell`` an (Muster ``test_contact_stage_audit_scope._seed_qualified_client``,
Refs #1765): eindeutige Pseudonyme und Falltitel, keine Mutation von
Seed-Datensätzen — die E2E-Datenbank teilen sich mehrere Module.

Gefiltert wird die Fallliste über ``?q=<Falltitel>`` (``search_fields = ["title"]``
in ``CaseListView``); die Titel tragen einen UUID-Suffix und sind damit
eindeutig. **Nicht** über das Pseudonym — die Fallsuche kennt nur den Titel.

Der Papierkorb liegt unter ``/cases/trash/`` und damit **nicht** unter
``/admin-mgmt/``: ``enter_sudo_mode`` ist hier nicht nötig.
"""

import re
import subprocess
import uuid

import pytest

from ._helpers import python_bin as _python

pytestmark = pytest.mark.e2e


def _shell(e2e_env, script):
    out = subprocess.run(
        [_python(), "src/manage.py", "shell", "--no-imports", "-c", script],
        capture_output=True,
        text=True,
        env=e2e_env,
        check=True,
    )
    return [ln for ln in out.stdout.strip().splitlines() if ln.strip()]


def _seed_case(e2e_env, *, stage, prefix):
    """Person der Stufe ``stage`` mit einem Fall anlegen.

    Liefert ``(pseudonym, titel, case_pk)``. Der Fall wird bewusst über die
    Shell erzeugt und nicht über ``/cases/new/``: das Formular hängt an einem
    Autocomplete über den Personenbestand, und die Kontaktstufe — die einzige
    Größe, um die es hier geht — ließe sich darüber gar nicht setzen.
    """
    marke = uuid.uuid4().hex[:8]
    pseudonym = f"E2E-{prefix}-Person-{marke}"
    titel = f"E2E-{prefix}-Fall-{marke}"
    script = (
        "from core.models import Case, Client, Facility;"
        " from core.models.user import User;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='emma');"
        f" c = Client.objects.create(facility=f, contact_stage='{stage}',"
        f" pseudonym='{pseudonym}', created_by=u);"
        f" k = Case.objects.create(facility=f, client=c, title='{titel}',"
        " description='E2E-Fallbeschreibung', created_by=u);"
        " print(k.pk)"
    )
    case_pk = _shell(e2e_env, script)[-1].strip()
    return pseudonym, titel, case_pk


def _seed_case_hold(e2e_env, case_pk):
    """Aktiver Legal Hold auf genau diesem Fall (P4)."""
    script = (
        "from core.models import Facility, LegalHold;"
        " from core.models.user import User;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='emma');"
        f" LegalHold.objects.create(facility=f, target_type='Case', target_id='{case_pk}',"
        " reason='E2E: Aufbewahrungspflicht', created_by=u)"
    )
    _shell(e2e_env, script)


def _case_rows(page, base_url, titel):
    """Zeilen der Fallliste zu genau diesem Titel."""
    page.goto(f"{base_url}/cases/?q={titel}")
    page.wait_for_load_state("domcontentloaded")
    page.locator("#case-table").wait_for(state="visible", timeout=30000)
    return page.locator(f"#case-table a:has-text('{titel}')")


def _delete_via_ui(page, base_url, case_pk, *, reason=None):
    """Löschseite des Falls aufrufen und absenden.

    Direkt über die URL statt über das ⋮-Menü: die Fallakte rendert ihre
    Aktionsleiste zweimal (Desktop/Mobil), und dass die Aktion überhaupt dort
    liegt, prüft :class:`TestDeleteActionInTheCaseFile` gezielt.
    """
    page.goto(f"{base_url}/cases/{case_pk}/delete/")
    page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/delete/$"))
    if reason is not None:
        page.locator("textarea[name='reason']").fill(reason)
    page.locator("[data-testid='case-delete-submit']").click()


class TestDeleteActionInTheCaseFile:
    """Die Aktion liegt in der Fallakte — und der Papierkorb nur bei der Betreuung."""

    def test_staff_sees_the_delete_action(self, staff_page, base_url, e2e_env):
        _pseudonym, _titel, case_pk = _seed_case(e2e_env, stage="identified", prefix="Aktion")
        page = staff_page
        page.goto(f"{base_url}/cases/{case_pk}/")
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("[data-testid='case-delete-request-btn']").count() >= 1

    def test_only_the_admin_sees_the_trash_link(self, staff_page, authenticated_page, base_url):
        staff_page.goto(f"{base_url}/cases/")
        staff_page.wait_for_load_state("domcontentloaded")
        assert staff_page.locator("[data-testid='case-trash-link']").count() == 0

        authenticated_page.goto(f"{base_url}/cases/")
        authenticated_page.wait_for_load_state("domcontentloaded")
        assert authenticated_page.locator("[data-testid='case-trash-link']").first.is_visible()


class TestDirectPathForANonQualifiedClient:
    """P1c, direkter Ausgang: keine zweite Person, kein Begründungsfeld."""

    def test_case_goes_straight_to_the_trash(self, staff_page, base_url, e2e_env):
        _pseudonym, titel, case_pk = _seed_case(e2e_env, stage="identified", prefix="Direkt")
        page = staff_page

        page.goto(f"{base_url}/cases/{case_pk}/delete/")
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("[data-testid='case-delete-direct-hint']").first.is_visible()
        assert page.locator("[data-testid='case-delete-four-eyes-hint']").count() == 0
        assert page.locator("textarea[name='reason']").count() == 0

        page.locator("[data-testid='case-delete-submit']").click()
        page.wait_for_url(re.compile(r"/cases/$"))

        # Unsichtbar: weder in der Liste noch über die Detail-URL.
        assert _case_rows(page, base_url, titel).count() == 0
        antwort = page.goto(f"{base_url}/cases/{case_pk}/")
        assert antwort is not None
        assert antwort.status == 404

    def test_event_of_the_trashed_case_keeps_the_person_but_loses_the_case(self, staff_page, base_url, e2e_env):
        """P5: das Ereignis bleibt in der Personenakte, nur der Rahmen fällt weg."""
        _pseudonym, titel, case_pk = _seed_case(e2e_env, stage="identified", prefix="Ereignis")
        script = (
            "from core.models import Case, DocumentType, Event;"
            " from core.models.user import User;"
            " from django.utils import timezone;"
            f" k = Case.objects.get(pk='{case_pk}');"
            " u = User.objects.get(username='emma');"
            " dt = DocumentType.objects.filter(facility=k.facility, entity_type='event',"
            " sensitivity='normal', is_active=True).first();"
            " e = Event.objects.create(facility=k.facility, client=k.client, document_type=dt,"
            " case=k, occurred_at=timezone.now(), data_json={}, created_by=u);"
            " print(e.pk)"
        )
        event_pk = _shell(e2e_env, script)[-1].strip()

        page = staff_page
        page.goto(f"{base_url}/events/{event_pk}/")
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("[data-testid='context-badge-case']").first.is_visible()

        _delete_via_ui(page, base_url, case_pk)
        page.wait_for_url(re.compile(r"/cases/$"))

        page.goto(f"{base_url}/events/{event_pk}/")
        page.wait_for_load_state("domcontentloaded")
        # Das Ereignis lebt weiter …
        assert page.locator("[data-testid='context-badge-client']").first.is_visible()
        # … aber ohne Falltitel und ohne toten Link auf die Fallakte.
        assert page.locator("[data-testid='context-badge-case']").count() == 0
        assert titel not in page.content()


class TestFourEyesPathForAQualifiedClient:
    """P1c, Antragsausgang: erst die zweite Person bringt den Fall in den Papierkorb."""

    def test_request_is_announced_and_visible_at_the_case(self, staff_page, base_url, e2e_env):
        _pseudonym, titel, case_pk = _seed_case(e2e_env, stage="qualified", prefix="Antrag")
        page = staff_page

        page.goto(f"{base_url}/cases/{case_pk}/delete/")
        page.wait_for_load_state("domcontentloaded")
        assert page.locator("[data-testid='case-delete-four-eyes-hint']").first.is_visible()
        assert page.locator("[data-testid='case-delete-confirmers']").first.is_visible()
        assert page.locator("textarea[name='reason']").count() == 1

        page.locator("textarea[name='reason']").fill("E2E: Fall versehentlich angelegt")
        page.locator("[data-testid='case-delete-submit']").click()
        page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/$"))

        # Der Fall bleibt nutzbar, der laufende Antrag ist am Datensatz sichtbar.
        assert page.locator("[data-testid='case-deletion-pending-badge']").first.is_visible()
        assert page.locator("[data-testid='case-deletion-pending-banner']").first.is_visible()
        assert page.locator("[data-testid='case-delete-request-pending']").count() >= 1
        assert _case_rows(page, base_url, titel).count() >= 1
        assert page.locator("[data-testid='case-row-deletion-pending-badge']").first.is_visible()

        # Zweiter Antrag ist wirkungslos und wird als solcher ausgewiesen.
        page.goto(f"{base_url}/cases/{case_pk}/delete/")
        assert page.locator("[data-testid='case-delete-already-pending']").first.is_visible()
        assert page.locator("textarea[name='reason']").count() == 0

    def test_second_person_approves_and_the_case_lands_in_the_trash(self, staff_page, lead_page, base_url, e2e_env):
        _pseudonym, titel, case_pk = _seed_case(e2e_env, stage="qualified", prefix="Genehmigt")
        _delete_via_ui(staff_page, base_url, case_pk, reason="E2E: Bestätigung durch Zweitperson")
        staff_page.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/$"))

        lead = lead_page
        lead.goto(f"{base_url}/deletion-requests/")
        lead.wait_for_load_state("domcontentloaded")
        karte = lead.locator(f"[data-testid='deletion-request-card']:has-text('{titel}')").first
        karte.wait_for(state="visible", timeout=30000)
        karte.locator("[data-testid='deletion-review-link']").click()
        lead.wait_for_url(re.compile(r"/review/$"))
        assert lead.locator("text=Fall-Löschantrag prüfen").first.is_visible()
        assert lead.locator(f"text={titel}").first.is_visible()

        lead.locator("button[name='action'][value='approve']").click()
        lead.wait_for_url(re.compile(r"/deletion-requests/$"))

        # Erst jetzt ist der Fall weg — für die antragstellende Fachkraft ebenso.
        assert _case_rows(staff_page, base_url, titel).count() == 0
        antwort = staff_page.goto(f"{base_url}/cases/{case_pk}/")
        assert antwort is not None
        assert antwort.status == 404


class TestTrashAndRestore:
    """Der Papierkorb ist der eine Ort, an dem ein gelöschter Fall sichtbar ist."""

    def test_admin_restores_the_case_into_the_list(self, staff_page, authenticated_page, base_url, e2e_env):
        _pseudonym, titel, case_pk = _seed_case(e2e_env, stage="identified", prefix="Restore")
        _delete_via_ui(staff_page, base_url, case_pk)
        staff_page.wait_for_url(re.compile(r"/cases/$"))

        admin = authenticated_page
        admin.goto(f"{base_url}/cases/trash/")
        admin.wait_for_load_state("domcontentloaded")
        assert admin.locator("[data-testid='case-trash-semantics-hint']").first.is_visible()
        zeile = admin.locator(f"tr:has-text('{titel}')").first
        zeile.wait_for(state="visible", timeout=30000)

        zeile.locator("[data-testid='case-restore-btn']").click()
        admin.wait_for_url(re.compile(r"/cases/[0-9a-f-]+/$"))

        assert _case_rows(admin, base_url, titel).count() >= 1
        admin.goto(f"{base_url}/cases/trash/")
        admin.wait_for_load_state("domcontentloaded")
        assert admin.locator(f"tr:has-text('{titel}')").count() == 0

    def test_anonymize_now_is_final_and_drops_the_restore_button(
        self, staff_page, authenticated_page, base_url, e2e_env
    ):
        _pseudonym, titel, case_pk = _seed_case(e2e_env, stage="identified", prefix="Tilgung")
        _delete_via_ui(staff_page, base_url, case_pk)
        staff_page.wait_for_url(re.compile(r"/cases/$"))

        admin = authenticated_page
        admin.goto(f"{base_url}/cases/trash/")
        admin.wait_for_load_state("domcontentloaded")
        zeile = admin.locator(f"tr:has-text('{titel}')").first
        zeile.wait_for(state="visible", timeout=30000)
        # ``data-confirm`` läuft über window.confirm — ohne Handler blockiert der Klick.
        admin.once("dialog", lambda dialog: dialog.accept())
        zeile.locator("[data-testid='case-anonymize-now-btn']").click()
        admin.wait_for_url(re.compile(r"/cases/trash/$"))

        # Der Titel ist weg, die Zeile bleibt — als getilgt ausgewiesen, ohne
        # „Wiederherstellen" (es holte nur eine leere Hülle zurück).
        assert admin.locator(f"tr:has-text('{titel}')").count() == 0
        assert admin.locator("[data-testid='case-trash-anonymized-badge']").first.is_visible()
        badge_zeile = admin.locator("tr:has([data-testid='case-trash-anonymized-badge'])").first
        assert badge_zeile.locator("[data-testid='case-restore-btn']").count() == 0


class TestLegalHoldBlocksTheFinalPurge:
    """P4: eine Aufbewahrungssperre hält auch die manuelle Tilgung an."""

    def test_hold_rejects_anonymize_now_and_keeps_the_case_restorable(
        self, staff_page, authenticated_page, base_url, e2e_env
    ):
        _pseudonym, titel, case_pk = _seed_case(e2e_env, stage="identified", prefix="Hold")
        _delete_via_ui(staff_page, base_url, case_pk)
        staff_page.wait_for_url(re.compile(r"/cases/$"))
        _seed_case_hold(e2e_env, case_pk)

        admin = authenticated_page
        admin.goto(f"{base_url}/cases/trash/")
        admin.wait_for_load_state("domcontentloaded")
        zeile = admin.locator(f"tr:has-text('{titel}')").first
        zeile.wait_for(state="visible", timeout=30000)
        admin.once("dialog", lambda dialog: dialog.accept())
        zeile.locator("[data-testid='case-anonymize-now-btn']").click()
        admin.wait_for_url(re.compile(r"/cases/trash/$"))

        assert admin.locator("text=Aufbewahrungssperre").first.is_visible()
        # Nichts getilgt: Titel und Wiederherstellen-Knopf stehen weiter.
        zeile = admin.locator(f"tr:has-text('{titel}')").first
        assert zeile.is_visible()
        assert zeile.locator("[data-testid='case-restore-btn']").count() == 1
