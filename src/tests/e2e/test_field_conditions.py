"""E2E-Tests: bedingte Feldlogik — Editor, Live-Sichtbarkeit, Hidden-Required (M1 P7, Refs #1057).

Deckt den Ende-zu-Ende-Fluss der Feld-zu-Feld-Bedingungen gegen den echten Server
(Port 8844) ab und ist aus der bereits erledigten **manuellen** Playwright-Runde
abgeleitet (0 CSP-Violations, Store-Fallback bestätigt). Szenarien (Plan-P7 a–e):

  (a) Regel-Editor am Wegwerf-DocType ``E2E-Cond-…``: Quell-Dropdown ohne
      file-/verschlüsselte Felder (Ziel-Dropdown MIT), Regel „Wenn <Zahl>
      greater_than 10 → zeige <Textbereich>" anlegen → Board zeigt sie; die
      Zyklus-Gegenrichtung wird mit Fehlermeldung abgewiesen (nicht persistiert).
  (b) Event-Formular: das konditionale Pflicht-Ziel ist initial versteckt
      (Beweiskraft-Assert VOR jeder Eingabe), erscheint bei Quellwert 15 SOFORT
      ohne Reload und verschwindet bei 5 wieder.
  (c) Hidden-Required: das versteckte Pflicht-Ziel blockiert das Absenden NICHT
      (kein Validierungsfehler → Redirect auf die Event-Detailseite).
  (d) ``track_console_errors`` über Editor- UND Event-Seite: 0 Errors (deckt CSP).
  (e) Cleanup in der geteilten E2E-DB (Risiko #9): Event soft-löschen, dann
      DocType + Feld-Templates best-effort entfernen.

Wegwerf-Konvention: alle erzeugten Objekte tragen das Präfix ``E2E-Cond-…`` mit
UUID-Suffix und werden in ``finally`` best-effort abgeräumt. Waits laufen über
``domcontentloaded`` und expect-Polling, NIE über ``networkidle``.

Hinweis Cleanup-Grenze: der erfolgreiche Submit aus (c) legt ein Event an;
``Event.document_type`` ist ``PROTECT``, das Soft-Delete lässt die Zeile bestehen
und der DocType-Delete-Blocker zählt sie weiter — der Wegwerf-DocType samt
Templates kann daher in derselben Session als (harmloses) Residuum verbleiben.
Die E2E-DB wird pro Session frisch ``seed --flush`` aufgesetzt (keine Kumulation).
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from ._helpers import track_console_errors

# M0-Muster wiederverwenden (test_config_ui.py bleibt unangetastet): DocType-CRUD
# und Feld-Board-Zuordnung sind dort stabil etabliert.
from .test_config_ui import (
    FIELDTEMPLATE_LIST,
    FORM_SUBMIT,
    _add_group,
    _assign_field,
    _assign_free_field,
    _create_doctype,
    _delete_doctype,
)

pytestmark = pytest.mark.e2e


def _cond_name(kind: str) -> str:
    """Eindeutiger Wegwerf-Name mit ``E2E-Cond``-Präfix."""
    return f"E2E-Cond-{kind}-{uuid.uuid4().hex[:6]}"


def _create_field_template(page, base_url, name, field_type, *, required=False) -> None:
    """Legt eine Wegwerf-Feldvorlage typisiert über die Config-UI an (M0-Muster).

    ``field_type`` steuert das Widget im Event-Formular (number→NumberInput,
    textarea→Textarea, file→FileInput); ``required`` setzt das Pflicht-Flag
    (``FieldTemplate.is_required``), das der Hidden-Required-Fall (c) braucht.
    """
    page.goto(f"{base_url}{FIELDTEMPLATE_LIST}new/", wait_until="domcontentloaded")
    page.fill("input#id_name", name)
    page.select_option("select#id_field_type", value=field_type)
    if required:
        page.check("#id_is_required")
    page.locator(FORM_SUBMIT).click()
    page.wait_for_url(re.compile(r"/config/field-templates/$"))


def _option_value_by_text(select_locator, text):
    """DTF-pk (Option-Value) einer Auswahl-Option, deren Label ``text`` enthält.

    Die Ziel-/Quell-Dropdowns sind ModelChoiceFields über ``DocumentTypeField``;
    das Option-Label ist ``"<DocType> → <FeldVorlage>"`` — der eindeutige
    Vorlagen-Name matcht als Substring.
    """
    option = select_locator.locator("option", has_text=text).first
    value = option.get_attribute("value")
    assert value, f"Option „{text}“ nicht im Dropdown gefunden"
    return value


def _wait_value_input(scope, expected_type):
    """Wartet, bis das typisierte Wert-Input (E19) sichtbar und vom erwarteten Typ ist.

    Quellfeld-/Operator-Wechsel laden das Partial per HTMX in den
    ``.condition-value-slot`` nach; nach beiden Swaps muss das Input dem Quelltyp
    entsprechen (number → ``type="number"``).
    """
    value_input = scope.locator('[data-testid="condition-value-input"]')
    expect(value_input).to_be_visible(timeout=10000)
    expect(value_input).to_have_attribute("type", expected_type, timeout=10000)
    return value_input


def _delete_field_template(page, base_url, name) -> None:
    """Best-effort-Cleanup einer Wegwerf-Feldvorlage (ignoriert Blocker/Fehler)."""
    try:
        page.goto(f"{base_url}{FIELDTEMPLATE_LIST}?q={name}", wait_until="domcontentloaded")
        row = page.locator(f'[data-testid="fieldtemplate-row"]:has-text("{name}")')
        if row.count() == 0:
            return
        row.first.locator('[data-testid="fieldtemplate-delete-link"]').click()
        page.wait_for_url(re.compile(r"/config/field-templates/[0-9a-f-]+/delete/$"), timeout=10000)
        confirm = page.locator('[data-testid="fieldtemplate-delete-confirm"]')
        if confirm.count() and confirm.is_enabled():
            confirm.click()
            page.wait_for_url(re.compile(r"/config/field-templates/$"), timeout=10000)
    except Exception:
        # Zugeordnete Vorlage (DTF-PROTECT) ist nicht löschbar — best-effort.
        pass


def _delete_event(page, base_url, event_url) -> None:
    """Best-effort: das in (c) angelegte anonyme Event soft-löschen."""
    try:
        match = re.search(r"/events/([0-9a-f-]+)/", event_url)
        if not match:
            return
        page.goto(f"{base_url}/events/{match.group(1)}/delete/", wait_until="domcontentloaded")
        reason = page.locator("textarea#id_reason")
        if reason.count():
            reason.fill("E2E-Cleanup")
        # Anonymes Event → Direktlösch-Zweig; der Button-Text ist auf der
        # Bestätigungsseite eindeutig (Logout-/Sprach-Submits heißen anders).
        # Vorher scopte `form:has(#id_reason)` — das Begründungsfeld rendert
        # bei anonymen Events aber gar nicht, das Cleanup war ein No-op.
        page.locator('button[type="submit"]:has-text("Endgültig löschen")').click()
        page.wait_for_url(lambda url: "/delete/" not in url, timeout=10000)
    except Exception:
        pass


class TestFieldConditionsEndToEnd:
    """Ein durchgehender Fluss (a)–(e); Cleanup im selben Test (geteilte E2E-DB)."""

    def test_editor_live_visibility_and_hidden_required(self, authenticated_page, base_url):
        page = authenticated_page
        # Ein Tracker über den GESAMTEN Fluss (Editor- UND Event-Seite) — Szenario (d).
        errors = track_console_errors(page)

        source_name = _cond_name("dauer")  # number (Quelle)
        target_name = _cond_name("notiz")  # textarea, Pflichtfeld (Ziel)
        file_name = _cond_name("beleg")  # file (nur zur Quell-Dropdown-Gegenprobe)
        doctype_name = f"E2E-Cond-{uuid.uuid4().hex[:8]}"

        # --- Setup: drei Feld-Templates ---------------------------------------
        _create_field_template(page, base_url, source_name, "number")
        _create_field_template(page, base_url, target_name, "textarea", required=True)
        _create_field_template(page, base_url, file_name, "file")

        # --- Setup: Wegwerf-DocType (ohne Systemtyp) + Zuordnung --------------
        doctype_pk = _create_doctype(page, base_url, doctype_name)
        created_event_url = None
        try:
            group = _cond_name("grp")
            _add_group(page, group)
            _assign_field(page, source_name, group)
            _assign_field(page, target_name, group)
            _assign_field(page, file_name, group)

            # #1718-Regression: Das Condition-Board rendert die „Neue Bedingung"-
            # Karte erst ab field_count >= 2. Der Feld-Board-Swap feuert jetzt
            # `refresh-conditions` (HX-Trigger) → das Bedingungs-Board lädt sich
            # OHNE Reload nach und die Karte erscheint. (Früher stand hier ein
            # `page.goto`-Workaround; sein Wegfall IST der Regressionstest.)
            add_card = page.locator('[data-testid="condition-add-card"]')
            expect(add_card).to_be_visible(timeout=10000)

            # (a) Quell-Dropdown OHNE file-Feld, Ziel-Dropdown MIT file-Feld.
            target_select = add_card.locator('[data-testid="condition-target"]')
            source_select = add_card.locator('[data-testid="condition-source"]')
            expect(source_select.locator("option", has_text=file_name)).to_have_count(0)
            expect(target_select.locator("option", has_text=file_name)).to_have_count(1)
            # Gegenprobe: die zulässige number-Quelle ist im Quell-Dropdown enthalten.
            expect(source_select.locator("option", has_text=source_name)).to_have_count(1)

            # (a) Regel anlegen: „Wenn <dauer> greater_than 10 → zeige <notiz>".
            target_select.select_option(value=_option_value_by_text(target_select, target_name))
            # Quellwechsel lädt den typabhängigen Operator-Slot (#1715) nach, dessen
            # `load`-Trigger anschließend das typisierte Wert-Input (#E19) nachzieht.
            with page.expect_response(lambda r: "value-input" in r.url and r.request.method == "GET"):
                source_select.select_option(value=_option_value_by_text(source_select, source_name))
            # #1715: Der Operator-Slot ist jetzt typgefiltert — number bietet
            # `greater_than`, aber NICHT `contains`.
            operator_select = add_card.locator('[data-testid="condition-operator"]')
            expect(operator_select.locator("option[value='greater_than']")).to_have_count(1, timeout=10000)
            expect(operator_select.locator("option[value='contains']")).to_have_count(0)
            with page.expect_response(lambda r: "value-input" in r.url and r.request.method == "GET"):
                operator_select.select_option(value="greater_than")
            _wait_value_input(add_card, "number").fill("10")
            add_card.locator('[data-testid="condition-submit"]').click()

            # Board zeigt die Regel (genau ein Zielblock, genau eine Zeile).
            target_block = page.locator('[data-testid="condition-target-block"]')
            expect(target_block).to_have_count(1, timeout=10000)
            expect(target_block).to_contain_text(target_name)
            expect(target_block).to_contain_text(source_name)
            expect(page.locator('[data-testid="condition-row"]')).to_have_count(1, timeout=10000)

            # (a) Zyklus-Gegenrichtung: „Wenn <notiz> is_not_empty → zeige <dauer>"
            # schließt den Kreis dauer→notiz→dauer → Fehlermeldung, keine Persistenz.
            add_card = page.locator('[data-testid="condition-add-card"]')
            target_select = add_card.locator('[data-testid="condition-target"]')
            source_select = add_card.locator('[data-testid="condition-source"]')
            target_select.select_option(value=_option_value_by_text(target_select, source_name))
            with page.expect_response(lambda r: "value-input" in r.url and r.request.method == "GET"):
                source_select.select_option(value=_option_value_by_text(source_select, target_name))
            with page.expect_response(lambda r: "value-input" in r.url and r.request.method == "GET"):
                add_card.locator('[data-testid="condition-operator"]').select_option(value="is_not_empty")
            # is_not_empty ist valueless → kein Wert-Input, sondern der Hinweis.
            expect(add_card.locator('[data-testid="condition-value-none"]')).to_be_visible(timeout=10000)
            add_card.locator('[data-testid="condition-submit"]').click()

            expect(page.locator('[data-testid="condition-form-errors"]')).to_be_visible(timeout=10000)
            # Inhaltlich die Zyklus-Meldung (nicht bloß irgendein Formfehler).
            expect(page.locator('[data-testid="condition-form-errors"]')).to_contain_text("Zyklus")
            # Die Zyklus-Bedingung wurde NICHT gespeichert: weiterhin genau eine Zeile.
            expect(page.locator('[data-testid="condition-row"]')).to_have_count(1, timeout=10000)

            # (b) Event-Formular: Live-Sichtbarkeit ohne Reload -----------------
            page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
            with page.expect_response(lambda r: "fields" in r.url and r.request.method == "GET"):
                page.select_option("select#id_document_type", label=doctype_name)

            dynamic = page.locator("#dynamic-fields")
            source_input = dynamic.locator("input[type='number']")
            source_input.wait_for(state="visible", timeout=10000)
            # Der conditionalField-Wrapper des Ziels ist der, der die textarea trägt
            # (x-show="visible" liegt auf dem [data-field-slug]-Wrapper).
            target_wrapper = dynamic.locator("[data-field-slug]:has(textarea)")

            # Beweiskraft: VOR jeder Eingabe ist das Ziel versteckt (Quelle leer ⇒
            # Regel unerfüllt). Bei ausgeschaltetem JS / fehlender Regel WÄRE es
            # sichtbar → dieser Assert würde rot.
            expect(target_wrapper).to_be_hidden(timeout=10000)

            # Quellwert über die Schwelle (15 > 10) → Ziel erscheint SOFORT.
            source_input.fill("")
            source_input.press_sequentially("15", delay=30)
            expect(target_wrapper).to_be_visible(timeout=10000)

            # Wieder darunter (5 < 10) → Ziel verschwindet.
            source_input.fill("")
            source_input.press_sequentially("5", delay=30)
            expect(target_wrapper).to_be_hidden(timeout=10000)

            # (c) Hidden-Required: das Pflicht-Ziel ist versteckt (Quelle = 5).
            # Absenden MUSS erfolgreich sein (kein Validierungsfehler) → Detailseite.
            page.locator("#event-submit-btn").click()
            page.wait_for_url(re.compile(r"/events/[0-9a-f-]+/$"), timeout=15000)
            created_event_url = page.url

            # (d) Editor- UND Event-Seite komplett CSP-/JS-fehlerfrei durchlaufen.
            assert errors == [], f"Konsolen-Errors/CSP-Verstöße (Editor+Event): {errors}"
        finally:
            # (e) Cleanup (geteilte E2E-DB): Event zuerst (PROTECT), dann DocType +
            # Templates — alles best-effort, damit ein Zwischenabbruch nicht sprengt.
            if created_event_url:
                _delete_event(page, base_url, created_event_url)
            _delete_doctype(page, base_url, doctype_pk)
            for name in (source_name, target_name, file_name):
                _delete_field_template(page, base_url, name)


def _with_board_refresh(page, action):
    """Führt eine board-mutierende Aktion aus und wartet IHREN Board-Refresh ab.

    Gruppen anlegen und Felder zuordnen antworten mit ``HX-Trigger:
    refresh-conditions`` — das Bedingungs-Board lädt sich daraufhin per hx-get
    komplett neu (outerHTML, ``condition_board.html``). Die Bestands-Helfer
    ``_add_group``/``_assign_field`` warten nur auf ihre eigene Zeile, nicht auf
    diesen Zweit-Swap. Wer danach sofort mit der „Neue Bedingung“-Karte spricht,
    verliert sie mitten im Gespräch: der verspätete Board-Swap ersetzt die
    Karte, laufende operator-/value-input-Antworten zielen auf entfernte
    Elemente und verpuffen kommentarlos (#1867-Diagnose per Netzwerk-Trace —
    im Trace fehlt nach dem Reset jede value-input-Anfrage, das Wert-Feld
    bleibt der Default-``text``-Slot der frischen Karte). Deshalb wird hier
    JEDE dieser Aktionen mit ihrem Board-GET verklammert; den Rest erledigt
    die Settle-Probe im Test (Ziel-Option des zuletzt zugeordneten Felds).
    """
    with page.expect_response(lambda r: "/conditions/board/" in r.url and r.request.method == "GET"):
        action()


def _add_greater_than_10_condition(page, target_name, source_name):
    """Legt über das Bedingungs-Board die Regel „Wenn <source_name> > 10 → zeige <target_name>" an.

    Quelle wählen lädt den typabhängigen Operator-Slot nach (#1715) — auf diese
    Antwort wird explizit gewartet, ebenso auf die value-input-Antwort des
    Operator-Wechsels. Voraussetzung: kein Board-Refresh mehr in flight (siehe
    ``_with_board_refresh``). Der DocType braucht mindestens zwei zugeordnete
    Felder, sonst rendert die „Neue Bedingung"-Karte gar nicht (#1718).
    """
    add_card = page.locator('[data-testid="condition-add-card"]')
    expect(add_card).to_be_visible(timeout=10000)
    target_select = add_card.locator('[data-testid="condition-target"]')
    source_select = add_card.locator('[data-testid="condition-source"]')
    target_select.select_option(value=_option_value_by_text(target_select, target_name))
    with page.expect_response(lambda r: "operator-input" in r.url and r.request.method == "GET"):
        source_select.select_option(value=_option_value_by_text(source_select, source_name))
    operator_select = add_card.locator('[data-testid="condition-operator"]')
    expect(operator_select.locator("option[value='greater_than']")).to_have_count(1, timeout=10000)
    with page.expect_response(lambda r: "value-input" in r.url and r.request.method == "GET"):
        operator_select.select_option(value="greater_than")
    _wait_value_input(add_card, "number").fill("10")
    add_card.locator('[data-testid="condition-submit"]').click()


class TestFieldGroupVisibilityEndToEnd:
    """Refs #1867: eine Gruppenkarte verschwindet komplett (inkl. Überschrift),
    wenn ALLE ihre Felder per Bedingung ausgeblendet sind; eine gemischte
    Gruppe (Bedingungs-Ziel + freies Feld) bleibt dagegen immer stehen.

    Wegwerf-DocType ``E2E-Cond-…`` wie im Fluss oben; kein Formular-Submit
    nötig — die Sichtbarkeits-/Toggle-Checks kommen ohne Event-Anlage aus.
    """

    def test_solo_group_hides_completely_mixed_group_stays_visible(self, authenticated_page, base_url):
        page = authenticated_page
        errors = track_console_errors(page)

        source_name = _cond_name("dauer")  # number, ungruppierte Quelle
        solo_target_name = _cond_name("solo")  # textarea, EINZIGES Feld seiner Gruppe
        mixed_target_name = _cond_name("mixt")  # text, Ziel in einer gemischten Gruppe
        mixed_free_name = _cond_name("frei")  # text, unkonditioniertes Feld derselben Gruppe
        doctype_name = f"E2E-Cond-{uuid.uuid4().hex[:8]}"
        solo_group_name = _cond_name("grpsolo")
        mixed_group_name = _cond_name("grpmixed")

        _create_field_template(page, base_url, source_name, "number")
        _create_field_template(page, base_url, solo_target_name, "textarea")
        _create_field_template(page, base_url, mixed_target_name, "text")
        _create_field_template(page, base_url, mixed_free_name, "text")

        doctype_pk = _create_doctype(page, base_url, doctype_name)
        try:
            _with_board_refresh(page, lambda: _add_group(page, solo_group_name))
            _with_board_refresh(page, lambda: _add_group(page, mixed_group_name))
            _with_board_refresh(page, lambda: _assign_free_field(page, source_name))
            _with_board_refresh(page, lambda: _assign_field(page, solo_target_name, solo_group_name))
            _with_board_refresh(page, lambda: _assign_field(page, mixed_target_name, mixed_group_name))
            _with_board_refresh(page, lambda: _assign_field(page, mixed_free_name, mixed_group_name))

            # Settle-Probe: Der LETZTE Board-Swap ist erst angewendet, wenn die
            # frische Karte das zuletzt zugeordnete Feld als Ziel-Option führt
            # (target_field-Queryset = alle Felder des DocTypes). Erst danach
            # ist die Interaktion mit der Karte racefrei.
            add_card = page.locator('[data-testid="condition-add-card"]')
            expect(
                add_card.locator('[data-testid="condition-target"] option', has_text=mixed_free_name)
            ).to_have_count(1, timeout=10000)

            _add_greater_than_10_condition(page, solo_target_name, source_name)
            expect(page.locator('[data-testid="condition-row"]')).to_have_count(1, timeout=10000)
            _add_greater_than_10_condition(page, mixed_target_name, source_name)
            expect(page.locator('[data-testid="condition-row"]')).to_have_count(2, timeout=10000)

            # --- Event-Formular: Gruppen-Sichtbarkeit ohne Reload -----------
            page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
            with page.expect_response(lambda r: "fields" in r.url and r.request.method == "GET"):
                page.select_option("select#id_document_type", label=doctype_name)

            dynamic = page.locator("#dynamic-fields")
            source_input = dynamic.locator("input[type='number']")
            source_input.wait_for(state="visible", timeout=10000)

            # Solo-Gruppe traegt den conditionalGroup-Wrapper (data-group-slugs);
            # die gemischte Gruppe traegt KEINEN — ihre Karte ist ein reines
            # ``.card``-Element ohne diesen Wrapper.
            solo_card = dynamic.locator(f"[data-group-slugs]:has(h2:has-text('{solo_group_name}'))")
            mixed_card = dynamic.locator(f".card:has(h2:has-text('{mixed_group_name}'))")

            # Beweiskraft: VOR jeder Eingabe ist die Solo-Gruppe komplett weg
            # (inkl. Überschrift), die gemischte Gruppe steht von Anfang an.
            expect(solo_card).to_be_hidden(timeout=10000)
            expect(mixed_card).to_be_visible(timeout=10000)
            expect(mixed_card.locator("h2")).to_contain_text(mixed_group_name)

            # Quellwert über die Schwelle (15 > 10) → Solo-Gruppe erscheint SOFORT.
            source_input.fill("")
            source_input.press_sequentially("15", delay=30)
            expect(solo_card).to_be_visible(timeout=10000)
            expect(solo_card.locator("h2")).to_contain_text(solo_group_name)

            # Wieder darunter (5 < 10) → Solo-Gruppe verschwindet erneut.
            source_input.fill("")
            source_input.press_sequentially("5", delay=30)
            expect(solo_card).to_be_hidden(timeout=10000)

            # Kontrollfall: die gemischte Gruppe blieb die ganze Zeit sichtbar.
            expect(mixed_card).to_be_visible(timeout=10000)

            assert errors == [], f"Konsolen-Errors/CSP-Verstöße: {errors}"
        finally:
            _delete_doctype(page, base_url, doctype_pk)
            for name in (source_name, solo_target_name, mixed_target_name, mixed_free_name):
                _delete_field_template(page, base_url, name)
