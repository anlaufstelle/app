"""E2E: Import-Wizard — Hochladen → Zuordnen → Probelauf → Übernehmen (M4, Refs #1060).

Manuell verifiziert am 2026-08-05 (Wizard-Roundtrip inkl. Fehlerpfad, Abbruch
und Übernahme); die Tests hier leiten sich aus den beobachteten Schritten ab
(Projekt-Konvention: E2E aus manueller Verifikation ableiten).

**Wegwerf-Daten-Strategie** (Lehre aus #1787/#1788): Kein Test mutiert
Seed-Objekte. Jeder Lauf bekommt über die modul-weite Fixture ``import_run``
ein eigenes Pseudonym-Präfix ``E2E-IMP-<hex8>``; alles, was die Tests anlegen
(Personen, Ereignisse, Importläufe), trägt dieses Präfix bzw. wird per PK
mitgeschrieben und am Modulende wieder entfernt. Das ist hier nicht Kosmetik:

* ``ImportJob.document_type`` ist PROTECT und ``ImportJob`` hängt sonst nur an
  ``Facility`` — ein liegengebliebener Ereignis-Lauf ließe den nächsten
  ``seed --flush`` (den das ``base_url``-Fixture zum Herstellen des
  Startzustands fährt) mit ``ProtectedError`` scheitern. ``flush_seed_data``
  räumt beide M4-Modelle seit Refs #1060 selbst ab; dieser Cleanup ist der
  Gürtel zum Hosenträger.
* Importierte Personen mit Seed-nahen Pseudonymen würden Listen-/Such-/
  Statistik-Tests anderer Dateien verschieben.

Die DB-Proben laufen über ``manage.py shell`` mit dem ``e2e_env``-Fixture —
nur so landet die Query auf der DB des jeweiligen Workers.
"""

import json
import re
import subprocess
import sys
import uuid

import pytest

pytestmark = pytest.mark.e2e

#: Wizard-URLs (Single Source of Truth für die Waits).
_MAPPING_RE = re.compile(r"/import/[0-9a-f-]{36}/zuordnung/$")
_PREVIEW_RE = re.compile(r"/import/[0-9a-f-]{36}/probelauf/$")
_DETAIL_RE = re.compile(r"/import/[0-9a-f-]{36}/$")


def _shell(e2e_env, code):
    """``manage.py shell -c`` auf der Worker-DB; liefert stdout.

    ``--no-imports`` hält die Shell frei von Auto-Importen; der Code muss
    deshalb alles selbst importieren. Nur ``;``-getrennte einfache Statements
    sind erlaubt (kein ``if``/``for``) — Schleifen daher als Comprehension.
    """
    result = subprocess.run(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", code],
        env=e2e_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"shell fehlgeschlagen: {result.stderr}\n{result.stdout}"
    return result.stdout


def _client_count(e2e_env, prefix):
    """Anzahl der (nicht gelöschten) Personen mit diesem Pseudonym-Präfix."""
    out = _shell(
        e2e_env,
        "from core.models import Client; "
        f"print('count=' + str(Client.objects.filter(pseudonym__startswith='{prefix}').count()))",
    )
    return int(next(line for line in out.splitlines() if line.startswith("count=")).removeprefix("count="))


@pytest.fixture(scope="module")
def import_run(e2e_env):
    """Modulweiter Wegwerf-Namensraum + Cleanup am Modulende.

    ``jobs`` sammelt die PKs aller im Modul angelegten Importläufe (aus den
    Wizard-URLs). Sie werden zuerst gelöscht: solange ein Lauf auf einen
    Dokumentationstyp zeigt, blockiert sein PROTECT-Verweis jede spätere
    Typ-Bereinigung.
    """
    state = {"prefix": f"E2E-IMP-{uuid.uuid4().hex[:8]}", "jobs": []}
    yield state
    prefix = state["prefix"]
    job_pks = ", ".join(f"'{pk}'" for pk in state["jobs"])
    # ``EventHistory`` trägt einen append-only-DB-Trigger; ein Ereignis lässt
    # sich ohne dessen kurzzeitige Abschaltung nicht löschen (identisches
    # Vorgehen wie ``core.seed.flush.flush_seed_data``). ``ENABLE ALWAYS``
    # statt ``ENABLE`` hält den gehärteten Ruhezustand aus Migration 0107.
    trigger_off = "ALTER TABLE core_eventhistory DISABLE TRIGGER eventhistory_no_delete"
    trigger_on = "ALTER TABLE core_eventhistory ENABLE ALWAYS TRIGGER eventhistory_no_delete"
    _shell(
        e2e_env,
        "from core.models import Client, Event, EventHistory, ImportJob; "
        "from django.db import connection; "
        f"ImportJob.objects.filter(pk__in=[{job_pks}]).delete(); "
        "cur = connection.cursor(); "
        f"cur.execute({trigger_off!r}); "
        f"EventHistory.objects.filter(event__client__pseudonym__startswith='{prefix}').delete(); "
        f"Event.objects.filter(client__pseudonym__startswith='{prefix}').delete(); "
        f"cur.execute({trigger_on!r}); "
        f"Client.objects.filter(pseudonym__startswith='{prefix}').delete(); "
        "print('cleanup ok')",
    )


def _write_csv(tmp_path, name, lines):
    """CSV mit Semikolon-Trennung und BOM-freiem UTF-8 schreiben."""
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _upload(page, base_url, csv_path, *, target_type, document_type_label=None):
    """Schritt 1: Datei hochladen; wartet auf die Zuordnungsseite.

    Trennzeichen wird bewusst explizit auf Semikolon gestellt statt der
    Sniffer-Automatik überlassen — eine dreispaltige Datei mit Freitext ist
    genau der Fall, in dem ``csv.Sniffer`` daneben liegen darf.

    Die Datei geht über den echten File-Chooser statt ``set_input_files``
    (Repo-Konvention aus ``test_file_vault``): deterministisch und unabhängig
    davon, wie das Widget gerendert wird.
    """
    page.goto(f"{base_url}/import/", wait_until="domcontentloaded")
    page.locator("[data-testid='import-new-link']").click()
    page.wait_for_url(re.compile(r"/import/neu/$"))

    page.locator(f"input[name='target_type'][value='{target_type}']").check()
    if document_type_label is not None:
        # Die Option traegt ``str(DocumentType)`` = „<Einrichtung> — <Name>";
        # ein ``label=``-Match waere exakt und damit an den Einrichtungsnamen
        # des Seeds gekettet. Deshalb ueber den Optionstext auflösen.
        option = page.locator("select[name='document_type'] option").filter(has_text=document_type_label).first
        page.select_option("select[name='document_type']", value=option.get_attribute("value"))
    page.select_option("select[name='delimiter']", value="semicolon")

    with page.expect_file_chooser() as fc_info:
        page.click("input[name='source_file']")
    fc_info.value.set_files(csv_path)

    page.locator("[data-testid='import-upload-submit']").click()
    page.wait_for_url(_MAPPING_RE)
    return re.search(r"/import/([0-9a-f-]{36})/", page.url).group(1)


def _map_columns(page, mapping):
    """Schritt 2: Zielfelder setzen und den Probelauf starten.

    ``mapping`` ist ``{Spaltenüberschrift: Zielschlüssel}``. Die Selects heißen
    ``column-<index>``; der Index wird über die erste Zelle der jeweiligen
    Zuordnungszeile aufgelöst, damit der Test nicht an der Spaltenreihenfolge
    der Datei klebt.
    """
    rows = page.locator("[data-testid='import-mapping-row']")
    for index in range(rows.count()):
        row = rows.nth(index)
        column_name = row.locator("td").first.inner_text().strip()
        target = mapping.get(column_name)
        if target is None:
            continue
        row.locator("[data-testid='import-mapping-select']").select_option(value=target)
    page.locator("[data-testid='import-mapping-submit']").click()


def _summary(page, testid):
    return page.locator(f"[data-testid='{testid}']").inner_text().strip()


class TestImportClientHappyPath:
    """Voller Roundtrip als Anwendungsbetreuung: 3 Personen aus einer CSV."""

    def test_upload_map_preview_commit_creates_three_clients(
        self, authenticated_page, base_url, e2e_env, import_run, tmp_path
    ):
        page = authenticated_page
        prefix = f"{import_run['prefix']}-OK"
        csv_path = _write_csv(
            tmp_path,
            "personen.csv",
            [
                "Pseudonym;Kontaktstufe;Altersgruppe;Notizen",
                f"{prefix}-1;Identifiziert;u18;Erstkontakt am Bahnhof",
                f"{prefix}-2;Qualifiziert;18_26;Beratung vereinbart",
                f"{prefix}-3;identified;27_plus;",
            ],
        )

        job_pk = _upload(page, base_url, csv_path, target_type="client")
        import_run["jobs"].append(job_pk)

        # Schritt 2: die Beispielwerte der Datei stehen sichtbar neben jeder
        # Spalte — ohne sie wäre die Zuordnung blindes Raten.
        samples = page.locator("[data-testid='import-mapping-samples']").first
        assert f"{prefix}-1" in samples.inner_text()
        assert page.locator("[data-testid='import-mapping-row']").count() == 4

        _map_columns(
            page,
            {
                "Pseudonym": "pseudonym",
                "Kontaktstufe": "contact_stage",
                "Altersgruppe": "age_cluster",
                "Notizen": "notes",
            },
        )
        page.wait_for_url(_PREVIEW_RE)

        # Schritt 3: fehlerfreier Probelauf …
        assert _summary(page, "import-summary-rows") == "3"
        assert _summary(page, "import-summary-created") == "3"
        assert _summary(page, "import-summary-errors") == "0"
        assert page.locator("[data-testid='import-error-table']").count() == 0

        # … und es ist wirklich noch nichts gespeichert (der Probelauf rollt
        # dieselbe Transaktion zurück, die die Übernahme committet).
        assert _client_count(e2e_env, prefix) == 0

        # Schritt 4: Übernahme
        page.locator("[data-testid='import-commit-submit']").click()
        page.wait_for_url(_DETAIL_RE)
        assert page.locator("text=Import übernommen").first.is_visible()
        assert _summary(page, "import-status") == "Übernommen"
        assert _summary(page, "import-detail-created") == "3"
        # ``summary.committed`` schaltet das Label von „Würden angelegt" auf
        # „Angelegte Datensätze" — der Unterschied zwischen Vorschau und Tatsache.
        assert page.locator("text=Angelegte Datensätze").first.is_visible()

        assert _client_count(e2e_env, prefix) == 3

        # Die Personen sind über die reguläre Liste erreichbar, nicht nur in der DB.
        page.goto(f"{base_url}/clients/?q={prefix}-2", wait_until="domcontentloaded")
        assert page.locator(f"text={prefix}-2").first.is_visible()


class TestImportErrorPath:
    """Ungültige Kontaktstufe: Fehlertabelle statt Übernahme — und ohne PII."""

    def test_invalid_contact_stage_blocks_commit_and_leaks_no_cell_value(
        self, authenticated_page, base_url, e2e_env, import_run, tmp_path
    ):
        page = authenticated_page
        prefix = f"{import_run['prefix']}-ERR"
        # Der ungültige Zellwert ist bewusst unverwechselbar: er darf im
        # gerenderten HTML der Fehlerseite NICHT vorkommen (PII-Invariante des
        # festen Meldungskatalogs, Refs #1060).
        bad_value = "Zwischenstufe-Vertrauensaufbau"
        csv_path = _write_csv(
            tmp_path,
            "personen-fehler.csv",
            [
                "Pseudonym;Kontaktstufe",
                f"{prefix}-1;{bad_value}",
                f"{prefix}-2;Qualifiziert",
            ],
        )

        job_pk = _upload(page, base_url, csv_path, target_type="client")
        import_run["jobs"].append(job_pk)

        _map_columns(page, {"Pseudonym": "pseudonym", "Kontaktstufe": "contact_stage"})
        page.wait_for_url(_PREVIEW_RE)

        assert _summary(page, "import-summary-rows") == "2"
        assert _summary(page, "import-summary-errors") == "1"
        assert _summary(page, "import-summary-created") == "1"

        error_rows = page.locator("[data-testid='import-error-row']")
        assert error_rows.count() == 1
        cells = error_rows.first.locator("td")
        # Zeile 2 = erste Datenzeile (Kopfzeile ist Zeile 1) — dieselbe Zählung,
        # die die Anwenderin in Excel sieht.
        assert cells.nth(0).inner_text().strip() == "2"
        assert cells.nth(1).inner_text().strip() == "Kontaktstufe"
        assert cells.nth(2).inner_text().strip() == "Wert ist keine zulässige Auswahl für dieses Feld."

        # PII-Invariante: Zeile und Spalte reichen; der Zellwert selbst bleibt draußen.
        assert bad_value not in page.content()

        # Solange Fehler bestehen, gibt es keinen Übernehmen-Knopf.
        assert page.locator("[data-testid='import-commit-submit']").count() == 0

        # Abbruch von der Vorschau aus (zwei Abbruch-Formulare gibt es nur auf
        # der Detailseite; hier ist es genau eines).
        page.locator("[data-testid='import-abort-submit']").click()
        page.wait_for_url(_DETAIL_RE)
        assert _summary(page, "import-status") == "Abgebrochen"
        assert page.locator("text=Die hochgeladene Datei wurde gelöscht").first.is_visible()

        # Ein abgebrochener Lauf hat nichts angelegt — auch nicht die fehlerfreie Zeile.
        assert _client_count(e2e_env, prefix) == 0


class TestImportEventWithDataJson:
    """Ereignis-Import mit Zusatzfeld: ``data_json``-Ziel aus dem Dokumentationstyp."""

    def test_event_import_writes_data_json_field(self, authenticated_page, base_url, e2e_env, import_run, tmp_path):
        page = authenticated_page
        prefix = f"{import_run['prefix']}-EVT"
        pseudonym = f"{prefix}-Person"
        note = "Import-Notiz aus CSV"

        # Ziel-Person als Wegwerf-Datensatz (kein Seed-Objekt mutieren).
        _shell(
            e2e_env,
            "from core.models import Client, Facility; "
            "f = Facility.objects.get(name='Hauptstelle'); "
            f"Client.objects.create(facility=f, pseudonym='{pseudonym}', contact_stage='identified'); "
            "print('client ok')",
        )

        csv_path = _write_csv(
            tmp_path,
            "ereignisse.csv",
            [
                "Zeitpunkt;Person;Notiz",
                f"05.08.2026 10:30;{pseudonym};{note}",
            ],
        )

        # Ereignis-Import braucht zwingend einen Dokumentationstyp — „Kontakt"
        # liefert mit ``notiz`` ein Nicht-FILE-Zusatzfeld als Zuordnungsziel.
        job_pk = _upload(page, base_url, csv_path, target_type="event", document_type_label="Kontakt")
        import_run["jobs"].append(job_pk)

        # Das dynamische Ziel steht als eigene Option im Select.
        options = page.locator("[data-testid='import-mapping-select']").first.locator("option")
        assert options.filter(has_text="Notiz").count() >= 1

        _map_columns(
            page,
            {"Zeitpunkt": "occurred_at", "Person": "client_pseudonym", "Notiz": "data_json.notiz"},
        )
        page.wait_for_url(_PREVIEW_RE)
        assert _summary(page, "import-summary-errors") == "0"
        assert _summary(page, "import-summary-created") == "1"

        page.locator("[data-testid='import-commit-submit']").click()
        page.wait_for_url(_DETAIL_RE)
        assert _summary(page, "import-status") == "Übernommen"

        # Das Ereignis existiert samt korrekt getipptem ``data_json``-Wert.
        out = _shell(
            e2e_env,
            "from core.models import Event; "
            f"e = Event.objects.filter(client__pseudonym='{pseudonym}').first(); "
            "print('event=' + __import__('json').dumps("
            "  {'exists': e is not None, 'doc': e.document_type.name if e else None, "
            "   'notiz': (e.data_json or {}).get('notiz') if e else None}))",
        )
        payload = json.loads(next(ln for ln in out.splitlines() if ln.startswith("event=")).removeprefix("event="))
        assert payload["exists"] is True
        assert payload["doc"] == "Kontakt"
        assert payload["notiz"] == note


class TestImportAccessControl:
    """Der Wizard ist Anwendungsbetreuung-only (Masseningest, konservativ gegated)."""

    def test_lead_gets_403_on_import_list(self, lead_page, base_url):
        resp = lead_page.goto(f"{base_url}/import/")
        assert resp.status == 403

    def test_lead_gets_403_on_import_create(self, lead_page, base_url):
        resp = lead_page.goto(f"{base_url}/import/neu/")
        assert resp.status == 403

    def test_staff_gets_403_on_import_list(self, staff_page, base_url):
        resp = staff_page.goto(f"{base_url}/import/")
        assert resp.status == 403
