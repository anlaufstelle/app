"""E2E: 4xx-Klartextantworten ersetzen den Arbeitsbereich nicht mehr (Refs #2385).

Nachtrag zu #2358/#2232: ``StatisticsView.get`` wechselte einen ungültigen
eigenen Zeitraum als Klartext in ``#statistics-content`` ein (das ganze
Dashboard verschwand bis zum Neuladen), ``RetentionHoldView.post`` dieselbe
Formularvalidierung in ``#proposal-<pk>`` (``hx-swap="outerHTML"`` — die ganze
Vorschlagskarte). Beide Antworten tragen jetzt ``HX-Reswap: none``
(``htmx_absage``, ``core/views/mixins.py``): der Arbeitsbereich bleibt stehen,
der globale Fehler-Toast (``#htmx-error-toast``, ``static/js/htmx-errors.js``)
zeigt die Meldung — Muster ``test_goals_htmx_art18_rejection.py``.
"""

from __future__ import annotations

import subprocess
import sys
import uuid

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

_TOAST = "#htmx-error-toast"


def _ist_zeitraum_wechsel(response):
    """GET auf ``/statistics/`` selbst — NICHT ``/statistics/chart-data/``.

    ``statisticsCharts`` (x-init) laedt seine Daten unabhaengig per fetch();
    ohne den Ausschluss koennte ``page.expect_response`` dessen Antwort statt
    der des "Anwenden"-Klicks einfangen (beide enthalten "/statistics/").
    """
    return response.request.method == "GET" and "/statistics/" in response.url and "chart-data" not in response.url


def _ensure_pending_proposal(e2e_env):
    """Legt EINE pending-RetentionProposal über eigene Wegwerf-Daten an.

    Reduzierte Fassung von ``test_retention_dashboard.py::_ensure_proposals``
    (dort ``count=3``, hier reicht eine Karte) — bewusst nicht importiert,
    damit diese Datei unabhängig von einer fremden Testdatei bleibt. Gründe
    für eigenen Wegwerf-Klient/-Events statt Bestandsdaten: siehe dort.
    """
    kennung = uuid.uuid4().hex[:8]
    code = (
        "from core.models import Client, DocumentType, Event, Facility, RetentionProposal; "
        "from datetime import date, timedelta; "
        "from django.utils import timezone; "
        "f = Facility.objects.get(name='Hauptstelle'); "
        "dt = DocumentType.objects.filter(facility=f, entity_type='event').first(); "
        f"c = Client.objects.create(facility=f, contact_stage='identified', pseudonym='HTMX4XX-{kennung}'); "
        "e = Event.objects.create(facility=f, client=c, document_type=dt, occurred_at=timezone.now(), data_json={}); "
        "p = RetentionProposal.objects.create(facility=f, target_type='Event', target_id=e.pk, "
        "  deletion_due_at=date.today() + timedelta(days=5), status='pending', retention_category='identified', "
        "  details={'pseudonym': c.pseudonym, 'document_type': dt.name if dt else None, "
        "           'occurred_at': str(e.occurred_at)}); "
        "print('proposal=' + str(p.pk))"
    )
    result = subprocess.run(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", code],
        env=e2e_env,
        capture_output=True,
        text=True,
    )
    pks = [ln.removeprefix("proposal=") for ln in result.stdout.splitlines() if ln.startswith("proposal=")]
    assert len(pks) == 1, f"Seed failed: {result.stderr}\n{result.stdout}"
    return pks[0]


def _ensure_number_tally_type(e2e_env):
    """Legt EINEN Dokumentationstyp mit Zahlen-Pflichtfeld für die Strichliste an.

    Eigene Wegwerfdaten (Kennung im Namen) statt Bestandstypen, damit die
    Karte auf der Zeitstrom-Seite eindeutig per Testid gefunden wird — Muster
    ``_ensure_pending_proposal`` oben.
    """
    kennung = uuid.uuid4().hex[:8]
    code = (
        "from core.models import DocumentType, DocumentTypeField, Facility, FieldTemplate; "
        "f = Facility.objects.get(name='Hauptstelle'); "
        f"dt = DocumentType.objects.create(facility=f, name='HTMX4XX-Zahl-{kennung}', tally_enabled=True); "
        f"ft = FieldTemplate.objects.create(facility=f, name='Ausgabe-{kennung}', "
        "  field_type=FieldTemplate.FieldType.NUMBER, is_required=True); "
        "DocumentTypeField.objects.create(document_type=dt, field_template=ft); "
        "print('doc_type=' + str(dt.pk))"
    )
    result = subprocess.run(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", code],
        env=e2e_env,
        capture_output=True,
        text=True,
    )
    pks = [ln.removeprefix("doc_type=") for ln in result.stdout.splitlines() if ln.startswith("doc_type=")]
    assert len(pks) == 1, f"Seed failed: {result.stderr}\n{result.stdout}"
    return pks[0]


def _disable_tally_opt_in(e2e_env, document_type_pk):
    """Simuliert eine Konfigurationsänderung in einem parallelen Tab/Fenster."""
    code = (
        "from core.models import DocumentType; "
        f"DocumentType.objects.filter(pk='{document_type_pk}').update(tally_enabled=False)"
    )
    result = subprocess.run(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", code],
        env=e2e_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Seed-Mutation fehlgeschlagen: {result.stderr}\n{result.stdout}"


class TestTallyAbbrechenFuerDeaktiviertenTypBleibtStehen:
    def test_absage_laesst_karte_stehen_und_zeigt_hinweis(self, lead_page, base_url, e2e_env):
        document_type_pk = _ensure_number_tally_type(e2e_env)
        page = lead_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")

        karte = page.locator(f"#tally-action-{document_type_pk}")
        expect(karte).to_be_visible(timeout=5000)
        karte.locator(f"[data-testid='tally-increment-{document_type_pk}']").click()
        cancel_link = karte.locator(f"[data-testid='tally-cancel-{document_type_pk}']")
        expect(cancel_link).to_be_visible(timeout=5000)

        # Ein anderer Tab deaktiviert den Typ, während das Werte-Formular offen bleibt.
        _disable_tally_opt_in(e2e_env, document_type_pk)

        with page.expect_response(lambda r: "cancel=1" in r.url) as info:
            cancel_link.click()
        response = info.value

        assert response.status == 400, response.status
        assert response.headers.get("hx-reswap") == "none"

        # Die Karte ist nicht durch den Meldungstext ersetzt worden — das
        # Werte-Formular ist weiterhin da (kein Swap wegen HX-Reswap: none).
        expect(karte).to_be_visible(timeout=5000)
        expect(cancel_link).to_be_visible()
        assert "nicht für die Strichliste freigegeben" not in karte.inner_text()

        toast = page.locator(_TOAST)
        expect(toast).to_be_visible(timeout=5000)
        expect(toast).to_contain_text("nicht für die Strichliste freigegeben")


class TestStatisticsUngueltigerZeitraumBleibtStehen:
    def test_absage_laesst_dashboard_stehen_und_zeigt_hinweis(self, lead_page, base_url):
        page = lead_page
        page.goto(f"{base_url}/statistics/", wait_until="domcontentloaded")

        kpi_vorher = page.locator("[data-testid='kpi-total-contacts']").inner_text()

        page.fill("#statistics-date-from", "2026-09-20")
        page.fill("#statistics-date-to", "2026-09-01")
        with page.expect_response(_ist_zeitraum_wechsel) as info:
            page.locator("form[aria-label='Eigener Zeitraum'] button[type='submit']").click()
        response = info.value

        assert response.status == 400, response.status
        assert response.headers.get("hx-reswap") == "none"

        # Das Dashboard ist nicht durch den Meldungstext ersetzt worden.
        content = page.locator("#statistics-content")
        expect(content).to_be_visible(timeout=5000)
        expect(page.locator("[data-testid='kpi-total-contacts']")).to_have_text(kpi_vorher)
        assert "darf nicht nach" not in content.inner_text()

        toast = page.locator(_TOAST)
        expect(toast).to_be_visible(timeout=5000)
        expect(toast).to_contain_text("darf nicht nach")

    def test_gegenprobe_gueltiger_zeitraum_wird_eingewechselt(self, lead_page, base_url):
        page = lead_page
        page.goto(f"{base_url}/statistics/", wait_until="domcontentloaded")

        page.fill("#statistics-date-from", "2026-01-01")
        page.fill("#statistics-date-to", "2026-01-31")
        with page.expect_response(_ist_zeitraum_wechsel) as info:
            page.locator("form[aria-label='Eigener Zeitraum'] button[type='submit']").click()
        response = info.value

        assert response.status == 200, response.status
        # ``[data-testid='statistics-period-range']`` steht nur auf dem
        # Berichte-Reiter (``{% if view_mode %}`` in full_content.html) — auf
        # der Übersicht selbst zeigen die neu gesetzten Eigener-Zeitraum-Felder
        # den übernommenen Zeitraum.
        expect(page.locator("#statistics-date-from")).to_have_value("2026-01-01")
        expect(page.locator("#statistics-date-to")).to_have_value("2026-01-31")
        assert page.locator(_TOAST).count() == 0


class TestRetentionHoldLeereBegruendungBleibtStehen:
    def test_absage_laesst_vorschlagskarte_stehen_und_zeigt_hinweis(self, lead_page, base_url, e2e_env):
        pk = _ensure_pending_proposal(e2e_env)
        page = lead_page
        page.goto(f"{base_url}/retention/", wait_until="domcontentloaded")

        karte = page.locator(f"#proposal-{pk}")
        expect(karte).to_be_visible(timeout=5000)
        karte.locator("button:has-text('Hold setzen')").click()
        reason_feld = karte.locator("textarea[name='reason']")
        expect(reason_feld).to_be_visible(timeout=5000)
        reason_feld.fill("   ")  # nur Leerraum: besteht die native required-Prüfung, scheitert serverseitig

        with page.expect_response(lambda r: "/hold/" in r.url) as info:
            karte.locator("button:has-text('Hold erstellen')").click()
        response = info.value

        assert response.status == 400, response.status
        assert response.headers.get("hx-reswap") == "none"

        # Die Karte ist nicht durch den Meldungstext ersetzt worden — Formular,
        # Badge und Aktionen sind weiterhin da.
        expect(karte).to_be_visible(timeout=5000)
        expect(karte.locator("textarea[name='reason']")).to_be_visible()
        expect(karte.locator("input[data-bulk-proposal]")).to_be_visible()
        assert "Begründung ist erforderlich" not in karte.inner_text()

        toast = page.locator(_TOAST)
        expect(toast).to_be_visible(timeout=5000)
        expect(toast).to_contain_text("Begründung ist erforderlich")

    def test_gegenprobe_gueltige_begruendung_wird_eingewechselt(self, lead_page, base_url, e2e_env):
        pk = _ensure_pending_proposal(e2e_env)
        page = lead_page
        page.goto(f"{base_url}/retention/", wait_until="domcontentloaded")

        karte = page.locator(f"#proposal-{pk}")
        karte.locator("button:has-text('Hold setzen')").click()
        karte.locator("textarea[name='reason']").fill("Gerichtsverfahren")

        with page.expect_response(lambda r: "/hold/" in r.url) as info:
            karte.locator("button:has-text('Hold erstellen')").click()
        response = info.value

        assert response.status == 200, response.status
        expect(page.locator(f"#proposal-{pk}")).to_contain_text("Legal Hold")
        assert page.locator(_TOAST).count() == 0
