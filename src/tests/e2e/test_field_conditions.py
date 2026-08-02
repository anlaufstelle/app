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
    DOCTYPE_LIST,
    FIELDTEMPLATE_LIST,
    FORM_SUBMIT,
    _add_group,
    _assign_field,
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

        # --- Setup: Wegwerf-DocType (system_type=note) + Zuordnung ------------
        doctype_pk = _create_doctype(page, base_url, doctype_name)
        created_event_url = None
        try:
            group = _cond_name("grp")
            _add_group(page, group)
            _assign_field(page, source_name, group)
            _assign_field(page, target_name, group)
            _assign_field(page, file_name, group)

            # Das Condition-Board rendert die „Neue Bedingung"-Karte erst ab
            # field_count >= 2 — der Feld-Board-Swap aktualisiert es nicht, daher
            # die Detailseite frisch laden.
            page.goto(f"{base_url}{DOCTYPE_LIST}{doctype_pk}/", wait_until="domcontentloaded")
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
            # Quell- und Operator-Wechsel laden je das typisierte Wert-Input per HTMX nach.
            with page.expect_response(lambda r: "value-input" in r.url and r.request.method == "GET"):
                source_select.select_option(value=_option_value_by_text(source_select, source_name))
            with page.expect_response(lambda r: "value-input" in r.url and r.request.method == "GET"):
                add_card.locator('[data-testid="condition-operator"]').select_option(value="greater_than")
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
