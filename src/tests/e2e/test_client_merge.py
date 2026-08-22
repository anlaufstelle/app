"""E2E: Duplikat-Merge für Personen — Direktweg, Vier-Augen, Papierkorb, Undo.

Refs #1876,
[ADR-045](../../../docs/adr/045-personen-merge.md). Gegenstück zu
``test_case_deletion_workflow.py`` auf der Merge-Achse; die Klickstrecken
folgen den Prüfsätzen unter ``## [Unreleased]`` in ``docs/dev/hand-checks.md``.

Die Weiche des Merges hängt an der **Kontaktstufe beider Personen** (ADR-045,
Entscheid 1) — jeder Test legt sich seine Personen über ``manage.py shell``
selbst an (Muster ``test_case_deletion_workflow._seed_case``): eindeutige
Pseudonyme mit gemeinsamem Präfix, keine Mutation von Seed-Datensätzen.

Angesprochen wird alles über UUIDs (Akten-URLs, ``data-dr-pk`` der
Antragskarte); die Personenliste wird nur über ihre **Pseudonym**-Suche
``?q=`` gefiltert — das ist deren designtes Suchfeld, und die Pseudonyme
tragen einen UUID-Suffix und sind damit eindeutig. Der Merge-eigene
Zielsuchschritt wird einmal (Direktweg) wirklich getippt; die übrigen Tests
springen per ``?target=<uuid>`` direkt auf die Bestätigungsseite.

Merge-Genehmigung/-Ablehnung läuft über ``/deletion-requests/`` und damit
**nicht** unter ``/admin-mgmt/``: ``enter_sudo_mode`` ist hier nicht nötig.
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


_CLEANUP = """
from core.models import Case, Client, Event, LegalHold, OfflineClientRevocation
from core.models.workitem import DeletionRequest, WorkItem
clients = Client.objects.filter(pseudonym__startswith="E2E-Merge-")
client_pks = [str(pk) for pk in clients.values_list("pk", flat=True)]
LegalHold.objects.filter(target_id__in=client_pks).delete()
DeletionRequest.objects.filter(target_id__in=client_pks).delete()
DeletionRequest.objects.filter(merge_into__in=clients).delete()
OfflineClientRevocation.objects.filter(client_pk__in=client_pks).delete()
Event.objects.filter(client__in=clients).delete()
WorkItem.objects.filter(client__in=clients).delete()
Case.objects.filter(client__in=clients).delete()
clients.update(merged_into=None)
clients.delete()
print("ok")
"""


@pytest.fixture(autouse=True)
def _merge_cleanup(e2e_env):
    """Räumt die Wegwerf-Personen dieser Datei ab — auch bei Testfehlern.

    Muster ``test_case_deletion_workflow`` (nach dem Fix 513486b1): gemeinsames
    Präfix ``E2E-Merge-`` an ALLEN Objekten, autouse-Aufräumen danach —
    zurückgelassene Personen kippen sonst über die Seitengröße 25 der
    alphabetisch sortierten Personenliste fremde Testdateien.

    Reihenfolge: Anträge und Widerrufs-Zeilen zuerst (freie ``target_id``s,
    ``DeletionRequest.merge_into`` ist PROTECT), dann Ereignisse und Fälle
    (löst das PROTECT auf ``Case.client``), dann ``merged_into`` nullen
    (PROTECT auf ``Client.merged_into`` — sonst schützt ein Tombstone sein
    eigenes Ziel vor dem Löschen), zuletzt die Personen.
    """
    yield
    _shell(e2e_env, _CLEANUP)


def _seed_client(e2e_env, *, stage, prefix):
    """Person der Stufe ``stage`` anlegen; liefert ``(pseudonym, pk)``."""
    marke = uuid.uuid4().hex[:8]
    pseudonym = f"E2E-Merge-{prefix}-{marke}"
    script = (
        "from core.models import Client, Facility;"
        " from core.models.user import User;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='emma');"
        f" c = Client.objects.create(facility=f, contact_stage='{stage}',"
        f" pseudonym='{pseudonym}', created_by=u);"
        " print(c.pk)"
    )
    pk = _shell(e2e_env, script)[-1].strip()
    return pseudonym, pk


def _seed_event(e2e_env, client_pk):
    """Ein Ereignis an der Person — der Wander-Beleg; liefert die Event-PK."""
    script = (
        "from core.models import Client, DocumentType, Event;"
        " from core.models.user import User;"
        " from django.utils import timezone;"
        f" c = Client.objects.get(pk='{client_pk}');"
        " u = User.objects.get(username='emma');"
        " dt = DocumentType.objects.filter(facility=c.facility, entity_type='event',"
        " sensitivity='normal', is_active=True).first();"
        " e = Event.objects.create(facility=c.facility, client=c, document_type=dt,"
        " occurred_at=timezone.now(), data_json={}, created_by=u);"
        " print(e.pk)"
    )
    return _shell(e2e_env, script)[-1].strip()


def _seed_merge(e2e_env, source_pk, target_pk):
    """Zusammenführung über den Service — Setup für Papierkorb-/Undo-Tests.

    Die UI-Strecke des Merges prüfen die Tests oben; der Papierkorb braucht
    nur den Zustand danach, nicht die Klicks davor.
    """
    script = (
        "from core.models import Client;"
        " from core.models.user import User;"
        " from core.services.client import merge_clients;"
        " u = User.objects.get(username='emma');"
        f" s = Client.objects.get(pk='{source_pk}');"
        f" t = Client.objects.get(pk='{target_pk}');"
        " merge_clients(s, t, u);"
        " print('ok')"
    )
    _shell(e2e_env, script)


def _request_merge_via_ui(page, base_url, source_pk, target_pk, *, reason):
    """Vier-Augen-Antrag über die Bestätigungsseite stellen (``?target=<uuid>``)."""
    page.goto(f"{base_url}/clients/{source_pk}/merge/?target={target_pk}")
    page.wait_for_load_state("domcontentloaded")
    page.locator("[data-testid='merge-four-eyes-hint']").first.wait_for(state="visible", timeout=30000)
    page.locator("textarea[name='reason']").fill(reason)
    page.locator("[data-testid='merge-request-submit']").click()
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))


def _open_review(page, base_url, e2e_env, source_pk):
    """Antragskarte über die Antrags-PK (UUID) finden und die Prüfseite öffnen."""
    dr_pk = _shell(
        e2e_env,
        "from core.models.workitem import DeletionRequest;"
        f" print(DeletionRequest.objects.get(target_id='{source_pk}', status='pending').pk)",
    )[-1].strip()
    page.goto(f"{base_url}/deletion-requests/")
    page.wait_for_load_state("domcontentloaded")
    karte = page.locator(f"[data-testid='deletion-request-card'][data-dr-pk='{dr_pk}']")
    karte.wait_for(state="visible", timeout=30000)
    assert karte.locator("[data-testid='deletion-merge-chip']").first.is_visible()
    karte.locator("[data-testid='deletion-review-link']").click()
    page.wait_for_url(re.compile(r"/review/$"))
    page.locator("[data-testid='merge-review-page']").wait_for(state="visible", timeout=30000)


class TestDirectMergeByTheLead:
    """ADR-045, Entscheid 1, Direktweg: zwei „Nur Pseudonym"-Akten, Leitung, keine Zweitperson."""

    def test_lead_merges_via_search_and_the_source_disappears(self, lead_page, base_url, e2e_env):
        quelle, quelle_pk = _seed_client(e2e_env, stage="identified", prefix="Direkt-Quelle")
        ziel, ziel_pk = _seed_client(e2e_env, stage="identified", prefix="Direkt-Ziel")
        event_pk = _seed_event(e2e_env, quelle_pk)
        page = lead_page

        # Zielsuchschritt: einmal wirklich getippt (HTMX-Nachladen).
        page.goto(f"{base_url}/clients/{quelle_pk}/merge/")
        page.wait_for_load_state("domcontentloaded")
        suchfeld = page.locator("[data-testid='merge-target-search']")
        suchfeld.wait_for(state="visible", timeout=30000)
        suchfeld.press_sequentially(ziel, delay=20)
        # Eindeutig über die Ziel-UUID im href — kein ``.first`` (Selektor-Guard).
        treffer = page.locator(f"[data-testid='merge-target-choose'][href*='{ziel_pk}']")
        treffer.wait_for(state="visible", timeout=30000)
        treffer.click()

        # Bestätigungsseite trägt die Beweislast: beide Akten + Warnungen.
        page.wait_for_url(re.compile(r"\?target="))
        page.locator("[data-testid='merge-source-column']").wait_for(state="visible", timeout=30000)
        assert page.locator("[data-testid='merge-target-column']").first.is_visible()
        assert page.locator("[data-testid='merge-statistics-warning']").first.is_visible()
        assert page.locator("[data-testid='merge-deadline-hint']").first.is_visible()
        assert page.locator("[data-testid='merge-four-eyes-hint']").count() == 0

        page.locator("[data-testid='merge-direct-submit']").click()
        page.wait_for_url(re.compile(rf"/clients/{ziel_pk}/$"))

        # Tombstone unsichtbar: Detail-URL 404, Personenliste ohne Zeile.
        antwort = page.goto(f"{base_url}/clients/{quelle_pk}/")
        assert antwort is not None
        assert antwort.status == 404
        page.goto(f"{base_url}/clients/?q={quelle}")
        page.wait_for_load_state("domcontentloaded")
        page.locator("#client-table").wait_for(state="visible", timeout=30000)
        assert page.locator(f"#client-table a:has-text('{quelle}')").count() == 0

        # Das Ereignis der Quelle hängt jetzt an der Ziel-Akte.
        page.goto(f"{base_url}/events/{event_pk}/")
        page.wait_for_load_state("domcontentloaded")
        badge = page.locator("[data-testid='context-badge-client']").first
        badge.wait_for(state="visible", timeout=30000)
        assert ziel_pk in (badge.get_attribute("href") or "")


class TestFourEyesPathForAQualifiedClient:
    """ADR-045, Entscheid 1, Antragsweg: erst die Zweitperson führt zusammen."""

    def test_staff_requests_and_the_second_person_approves(self, staff_page, lead_page, base_url, e2e_env):
        quelle, quelle_pk = _seed_client(e2e_env, stage="qualified", prefix="Antrag-Quelle")
        _ziel, ziel_pk = _seed_client(e2e_env, stage="identified", prefix="Antrag-Ziel")

        _request_merge_via_ui(staff_page, base_url, quelle_pk, ziel_pk, reason="E2E: doppelt angelegt")

        # Der laufende Antrag ist an der Akte sichtbar — als Zusammenführung,
        # nicht als Löschung.
        assert staff_page.locator("[data-testid='client-merge-pending-badge']").first.is_visible()
        assert staff_page.locator("[data-testid='client-merge-pending-banner']").first.is_visible()

        _open_review(lead_page, base_url, e2e_env, quelle_pk)
        lead_page.locator("button[name='action'][value='approve']").click()
        lead_page.wait_for_url(re.compile(r"/deletion-requests/$"))

        # Erst jetzt ist die Quelle weg — für die antragstellende Fachkraft ebenso.
        antwort = staff_page.goto(f"{base_url}/clients/{quelle_pk}/")
        assert antwort is not None
        assert antwort.status == 404

    def test_rejection_leaves_both_clients_untouched(self, staff_page, lead_page, base_url, e2e_env):
        quelle, quelle_pk = _seed_client(e2e_env, stage="qualified", prefix="Ablehnung-Quelle")
        _ziel, ziel_pk = _seed_client(e2e_env, stage="identified", prefix="Ablehnung-Ziel")

        _request_merge_via_ui(staff_page, base_url, quelle_pk, ziel_pk, reason="E2E: Ablehnung erwartet")

        _open_review(lead_page, base_url, e2e_env, quelle_pk)
        lead_page.locator("button[name='action'][value='reject']").click()
        lead_page.wait_for_url(re.compile(r"/deletion-requests/$"))

        # Nichts wanderte: die Quelle lebt, der Wartezustand ist aufgehoben.
        staff_page.goto(f"{base_url}/clients/{quelle_pk}/")
        staff_page.wait_for_load_state("domcontentloaded")
        assert staff_page.locator(f"text={quelle}").first.is_visible()
        assert staff_page.locator("[data-testid='client-merge-pending-badge']").count() == 0
        antwort = staff_page.goto(f"{base_url}/clients/{ziel_pk}/")
        assert antwort is not None
        assert antwort.status == 200


class TestTrashAndUndo:
    """Der Papierkorb weist den Tombstone aus — und trägt den einen Rückweg."""

    def test_trash_names_the_target_and_offers_the_undo_instead_of_restore(self, authenticated_page, base_url, e2e_env):
        quelle, quelle_pk = _seed_client(e2e_env, stage="identified", prefix="Korb-Quelle")
        ziel, ziel_pk = _seed_client(e2e_env, stage="identified", prefix="Korb-Ziel")
        _seed_merge(e2e_env, quelle_pk, ziel_pk)

        admin = authenticated_page
        admin.goto(f"{base_url}/clients/trash/")
        admin.wait_for_load_state("domcontentloaded")
        zeile = admin.locator(f"tr:has-text('{quelle}')").first
        zeile.wait_for(state="visible", timeout=30000)

        hinweis = zeile.locator("[data-testid='client-trash-merged-into']")
        assert hinweis.first.is_visible()
        assert ziel in (hinweis.first.text_content() or "")
        # Statt des wirkungslosen „Wiederherstellen" steht das Aufheben.
        assert zeile.locator("[data-testid='client-unmerge-btn']").count() == 1
        assert zeile.locator(f"form[action='/clients/{quelle_pk}/restore/']").count() == 0

    def test_admin_reverts_the_merge_and_the_data_returns(self, authenticated_page, base_url, e2e_env):
        quelle, quelle_pk = _seed_client(e2e_env, stage="identified", prefix="Undo-Quelle")
        _ziel, ziel_pk = _seed_client(e2e_env, stage="identified", prefix="Undo-Ziel")
        event_pk = _seed_event(e2e_env, quelle_pk)
        _seed_merge(e2e_env, quelle_pk, ziel_pk)

        admin = authenticated_page
        admin.goto(f"{base_url}/clients/trash/")
        admin.wait_for_load_state("domcontentloaded")
        zeile = admin.locator(f"tr:has-text('{quelle}')").first
        zeile.wait_for(state="visible", timeout=30000)
        # ``data-confirm`` läuft über window.confirm — ohne Handler blockiert der Klick.
        admin.once("dialog", lambda dialog: dialog.accept())
        zeile.locator("[data-testid='client-unmerge-btn']").click()
        admin.wait_for_url(re.compile(rf"/clients/{quelle_pk}/$"))

        # Die Quelle ist wieder da und ihr Ereignis zurückgewandert.
        assert admin.locator(f"text={quelle}").first.is_visible()
        admin.goto(f"{base_url}/events/{event_pk}/")
        admin.wait_for_load_state("domcontentloaded")
        badge = admin.locator("[data-testid='context-badge-client']").first
        badge.wait_for(state="visible", timeout=30000)
        assert quelle_pk in (badge.get_attribute("href") or "")

        admin.goto(f"{base_url}/clients/trash/")
        admin.wait_for_load_state("domcontentloaded")
        assert admin.locator(f"tr:has-text('{quelle}')").count() == 0


class TestSimilarHintOnTheCreateForm:
    """Der präventive Teil: die Anlage warnt beim Tippen — ohne zu blockieren."""

    def test_typing_an_existing_pseudonym_shows_the_hint(self, staff_page, base_url, e2e_env):
        bestand, bestand_pk = _seed_client(e2e_env, stage="identified", prefix="Hinweis")

        page = staff_page
        page.goto(f"{base_url}/clients/new/")
        page.wait_for_load_state("domcontentloaded")
        feld = page.locator("#id_pseudonym")
        feld.wait_for(state="visible", timeout=30000)
        feld.press_sequentially(bestand, delay=20)

        hinweis = page.locator("[data-testid='client-similar-hint']")
        hinweis.first.wait_for(state="visible", timeout=30000)
        link = page.locator("[data-testid='client-similar-link']").first
        assert bestand in (link.text_content() or "")
        assert bestand_pk in (link.get_attribute("href") or "")
        # Nicht-blockierend: der Speichern-Knopf bleibt aktiv erreichbar.
        assert page.locator("button[type='submit']").first.is_enabled()
