"""E2E-Tests: dynamische Zusatzfelder an Personen (M2 P15b, Refs #1058).

Abgeleitet aus der manuellen Playwright-Runde P15a (`/work/m2-artifacts/p15a-manual-report.md`,
alle Schritte PASS) — Projektkonvention: E2E-Tests entstehen aus der beobachteten
Handrunde, nicht vorab. Abgedeckt sind zwei Flüsse:

  (1) **Ganzfluss** ``test_client_dynamic_fields_full_roundtrip``: Wegwerf-Personen-Typ
      über die Config-UI anlegen (ein normales + ein verschlüsseltes Feld über
      Feldvorlagen), Person mit Typ anlegen (Typ-Select → HTMX-Swap → Felder füllen),
      Detail zeigt beide Werte im **Klartext** (der verschlüsselte kommt entschlüsselt
      zurück), Bearbeiten zeigt sie vorbefüllt und speichert eine Änderung, der
      Typwechsel-Warnhinweis ist sichtbar und der Wechsel **verwirft** die Altwerte.
      Zusätzlich zwei Abgrenzungen: das Personen-Select zeigt nur ``entity_type=client``
      (kein Seed-Ereignistyp) und der Ereignis-Picker zeigt die Personen-Typen nicht.

  (2) **Regression E-11/E-12** ``test_inactive_assigned_type_survives_switch_away_and_back``
      (P7): Ist-Typ einer Person nachträglich deaktiviert → das Select bietet
      ihn weiter an, markiert mit „(deaktiviert)"; ein Wechsel weg auf einen anderen Typ
      und **zurück** liefert vom Partial ``200`` mit den eigenen Feldern (vor dem Fix:
      404 → HTMX swapt bei 4xx nicht → im DOM blieben die Felder des anderen Typs →
      der folgende, völlig plausible Submit leerte die Bestandswerte still).
      Seit Refs #1758 prüft derselbe Test zusätzlich, dass das zurückgeholte Feldset
      die Bestandswerte **vorbefüllt** zeigt: der Rückweg auf den gespeicherten Typ
      ist kein Typwechsel, das Partial liefert dafür ``initial_data`` mit (E11
      „Typwechsel verwirft" gilt unverändert für einen echten Wechsel). Damit
      überstehen Typ **und** Werte den Umweg auch ohne erneute Eingabe.

  (3) **Rückfrage vor dem Verwerfen** ``test_confirm_only_when_values_are_at_risk_and_dismiss_keeps_data``
      (Refs #1733): Anlage und Speichern ohne Typwechsel fragen nicht nach; das
      Entfernen des Typs an einer Person mit befülltem Zusatzfeld fragt nach, nennt
      die Feldanzahl, und ein Abbruch lässt Formular wie Datensatz unangetastet.

Wegwerf-Konvention (die E2E-DB ist geteilt!): alle Objekte tragen das Präfix
``E2E-Cli-…`` mit uuid4-Suffix und werden in ``finally`` best-effort abgeräumt —
zuerst wird der Typ von der Person gelöst („— Kein Typ —"), weil
``Client.document_type`` PROTECT ist und der Lösch-Dialog sonst blockiert.
Cleanup-Grenze: die Wegwerf-**Person** bleibt bestehen (die UI kennt nur den
Vier-Augen-Löschworkflow); die E2E-DB wird pro Session frisch ``seed --flush``
aufgesetzt, es kumuliert also nichts.

Waits laufen über ``expect``-Polling, ``domcontentloaded`` und
``expect_response`` — NIE über ``networkidle``.
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from ._helpers import track_console_errors

# M0-/M1-Muster wiederverwenden (test_config_ui.py bleibt unangetastet): die
# Config-CRUD-Fläche und die Board-Zuordnung sind dort stabil etabliert.
from .test_config_ui import (
    _DETAIL_RE,
    DOCTYPE_LIST,
    FIELDTEMPLATE_LIST,
    FORM_SUBMIT,
    _assign_free_field,
)
from .test_field_conditions import _delete_field_template

pytestmark = pytest.mark.e2e

CLIENT_DETAIL_RE = re.compile(r"/clients/([0-9a-f-]+)/$")
DYNAMIC_SECTION = "[data-testid='dynamic-data-section']"
DOC_TYPE_SELECT = "select#id_document_type"
# Nie ein nackter ``button[type=submit]`` — der matcht auch Navigations-/Logout-/
# Sprach-Buttons (e2e-runbook § 4).
ENTITY_SUBMIT = "#main-content button[type='submit']"


# ---------------------------------------------------------------------------
# Wegwerf-Namen + Config-UI-Helfer (auch vom Fall-Pendant importiert)
# ---------------------------------------------------------------------------


def _cli_name(kind: str) -> str:
    """Eindeutiger Wegwerf-Name mit ``E2E-Cli``-Präfix."""
    return f"E2E-Cli-{kind}-{uuid.uuid4().hex[:6]}"


def _slug_of(name: str) -> str:
    """Slug einer Wegwerf-Feldvorlage.

    Die Namen hier sind bewusst ASCII + Bindestriche, ``slugify`` reduziert sie
    damit auf reines ``lower()`` (gleiche Annahme wie ``test_config_ui``).
    """
    return name.lower()


def _create_field_template(page, base_url, name, field_type, *, encrypted=False) -> None:
    """Wegwerf-Feldvorlage über die Config-UI anlegen (M1-Muster + ``is_encrypted``).

    ``encrypted=True`` setzt das Feld auf Feldverschlüsselung; die Sensibilität
    bleibt bewusst auf dem Formular-Default (Normal) — der Model-Invariant
    „Sensibilität=Hoch ⇒ verschlüsselt" gilt nur in dieser Richtung, und ein
    HIGH-Feld würde in der Detailanzeige zusätzlich der Rollen-Beschränkung
    unterliegen (hier nicht Prüfgegenstand).
    """
    page.goto(f"{base_url}{FIELDTEMPLATE_LIST}new/", wait_until="domcontentloaded")
    page.fill("input#id_name", name)
    page.select_option("select#id_field_type", value=field_type)
    if encrypted:
        page.check("#id_is_encrypted")
    page.locator(FORM_SUBMIT).click()
    page.wait_for_url(re.compile(r"/config/field-templates/$"))


def _create_entity_doctype(page, base_url, name, entity_type) -> str:
    """Wegwerf-Dokumentationstyp für ``entity_type`` (``client``/``case``) anlegen.

    ``system_type`` bleibt LEER: ``ConfigDocumentTypeForm.clean`` lässt einen
    Systemtyp nur bei ``entity_type=event`` zu. Gibt die UUID aus der Detail-URL
    zurück.
    """
    page.goto(f"{base_url}{DOCTYPE_LIST}new/", wait_until="domcontentloaded")
    page.fill("input#id_name", name)
    page.select_option("select#id_entity_type", value=entity_type)
    page.locator(FORM_SUBMIT).click()
    page.wait_for_url(_DETAIL_RE)
    match = _DETAIL_RE.search(page.url)
    assert match, f"Detail-URL nicht erkannt: {page.url}"
    return match.group(1)


def _set_doctype_active(page, base_url, pk, active) -> None:
    """Dokumentationstyp über das Bearbeiten-Formular (de)aktivieren."""
    page.goto(f"{base_url}{DOCTYPE_LIST}{pk}/edit/", wait_until="domcontentloaded")
    checkbox = page.locator("#id_is_active")
    if active:
        checkbox.check()
    else:
        checkbox.uncheck()
    page.locator(FORM_SUBMIT).click()
    page.wait_for_url(re.compile(rf"/config/document-types/{pk}/$"))


def _delete_doctype_safe(page, base_url, pk) -> None:
    """Best-effort-Cleanup eines Wegwerf-Typs — ohne Hänger am disabled Button.

    ``confirm_delete.html`` deaktiviert „Endgültig löschen", solange Ereignisse/
    Personen/Fälle am Typ hängen (PROTECT). Ein Klick darauf würde in den
    Actionability-Timeout laufen; deshalb wird der Zustand vorher geprüft.
    """
    try:
        page.goto(f"{base_url}{DOCTYPE_LIST}{pk}/delete/", wait_until="domcontentloaded")
        confirm = page.locator('[data-testid="doctype-delete-confirm"]')
        if confirm.count() == 0 or not confirm.is_enabled():
            return
        confirm.click()
        page.wait_for_url(re.compile(r"/config/document-types/$"), timeout=10000)
    except Exception:
        # Cleanup ist best-effort — ein bereits gelöschter/kaputter Zustand darf
        # den Test-Abschluss nicht sprengen.
        pass


def _detach_document_type(page, base_url, edit_url, detail_re) -> None:
    """Wegwerf-Typ von Person/Fall lösen (Select auf „— Kein Typ —" + speichern).

    Muss VOR ``_delete_doctype_safe`` laufen: ``Client.document_type`` /
    ``Case.document_type`` sind PROTECT, der Lösch-Dialog wäre sonst blockiert.
    Nebeneffekt (gewollt): der Service leert dabei ``data_json`` — auch der
    verschlüsselte Wegwerf-Wert verschwindet.

    Refs #1733: genau dieser Nebeneffekt löst jetzt die Rückfrage vor dem
    Speichern aus. Ohne Dialog-Handler dismisst Playwright sie automatisch, der
    Submit würde abgebrochen und das Aufräumen liefe still ins Leere.
    """
    try:
        page.goto(edit_url, wait_until="domcontentloaded")
        select = page.locator(DOC_TYPE_SELECT)
        if select.count() == 0:
            return
        select.select_option(value="")
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator(ENTITY_SUBMIT).click()
        page.wait_for_url(detail_re, timeout=15000)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Formular-Helfer (HTMX-Swap)
# ---------------------------------------------------------------------------


def _stamp_dynamic_fields(page):
    """Alle aktuellen Kinder von ``#dynamic-fields`` als „vor dem Swap" markieren.

    Muster aus ``test_file_vault.select_doc_type_and_wait`` (Refs #1797): das
    Ziel ist bereits server-seitig gerendert, ein nacktes Warten auf einen
    Selektor traefe also den VOR-Swap-Knoten.
    """
    page.evaluate(
        "() => document.querySelectorAll('#dynamic-fields > *').forEach((el) => el.setAttribute('data-pre-swap', '1'))"
    )


def _await_dynamic_fields_swap(page):
    """Warten, bis KEIN gestempelter Knoten mehr unter ``#dynamic-fields`` haengt.

    Auf das Verschwinden der Alt-Knoten zu warten statt auf das Erscheinen eines
    neuen deckt beide Faelle ab: einen Typ MIT Zusatzfeldern und den leeren Typ
    (``value=""``), dessen Partial gar keine Kinder liefert.
    """
    page.wait_for_selector("#dynamic-fields > [data-pre-swap]", state="detached", timeout=10000)


def switch_document_type(page, *, entity="clients", label=None, value=None, expected_status=200):
    """Typ im Select wechseln und den HTMX-Nachladevorgang abwarten.

    Der Wechsel lädt die Zusatzfelder über ``/partials/<entity>/fields/`` nach
    (``hx-trigger="change"``). Auf die Antwort wird explizit gewartet und ihr
    Status geprüft — ein 4xx ist der Kern des E-12-Regressionsfalls: HTMX swapt
    dann NICHT, und ohne diese Assertion sähe man nur die Folgefehler.

    Review (Refs #1855): Die Antwort allein ist noch KEIN
    Swap. Deshalb werden die Alt-Knoten vor dem Wechsel gestempelt und nach der
    Antwort ihr Verschwinden abgewartet — erst danach ist ein ``fill`` sicher.
    Bei erwartetem 4xx entfaellt das, weil htmx dann bewusst nicht swapt.
    """
    _stamp_dynamic_fields(page)
    with page.expect_response(
        lambda r: f"/partials/{entity}/fields/" in r.url and r.request.method == "GET"
    ) as response_info:
        if label is not None:
            page.select_option(DOC_TYPE_SELECT, label=label)
        else:
            page.select_option(DOC_TYPE_SELECT, value=value)
    status = response_info.value.status
    assert status == expected_status, f"Felder-Partial antwortete {status} statt {expected_status}"
    if expected_status == 200:
        _await_dynamic_fields_swap(page)


def fill_dynamic_field(page, selector, value):
    """Ein HTMX-nachgeladenes Feld befüllen — EINMAL, nach synchronisiertem Swap.

    Review (Refs #1855): Hier stand eine Poll-Schleife, deren
    Prädikat selbst ``fill`` aufrief und den Wert sofort danach las — sie war
    schon beim ersten Durchlauf wahr und damit gar keine Barriere. Die
    Synchronisation gehört an den Swap (``switch_document_type``); hier bleibt
    nur das Füllen plus eine auto-wartende Wert-Assertion.
    """
    field = page.locator(selector)
    field.wait_for(state="visible", timeout=10000)
    field.fill(value)
    expect(field).to_have_value(value, timeout=5000)


def create_client(page, base_url, pseudonym) -> str:
    """Wegwerf-Person ohne Typ anlegen; gibt die UUID aus der Detail-URL zurück."""
    page.goto(f"{base_url}/clients/new/", wait_until="domcontentloaded")
    page.fill('input[name="pseudonym"]', pseudonym)
    page.locator(ENTITY_SUBMIT).click()
    page.wait_for_url(CLIENT_DETAIL_RE)
    match = CLIENT_DETAIL_RE.search(page.url)
    assert match, f"Personen-Detail-URL nicht erkannt: {page.url}"
    return match.group(1)


# ---------------------------------------------------------------------------
# (1) Ganzfluss
# ---------------------------------------------------------------------------


class TestClientDynamicFields:
    """Anlegen → Detail (Klartext) → Bearbeiten (Prefill) → Typwechsel (verwirft)."""

    def test_client_dynamic_fields_full_roundtrip(self, authenticated_page, base_url):
        page = authenticated_page
        # Ein Tracker über den GESAMTEN Fluss (Config-UI, Personen-Formular,
        # Detail, Ereignis-Picker) — 0 Konsolen-Errors deckt die CSP-Prüfung ab.
        errors = track_console_errors(page)

        ft_plain = _cli_name("treff")  # text, unverschlüsselt
        ft_secret = _cli_name("geheim")  # textarea, verschlüsselt
        ft_other = _cli_name("kurz")  # text, gehört zum Wechselziel
        slug_plain, slug_secret, slug_other = (_slug_of(n) for n in (ft_plain, ft_secret, ft_other))
        type_a = _cli_name("TypA")
        type_b = _cli_name("TypB")

        value_plain = "Bahnhofsvorplatz Nordseite"
        value_plain_new = "Suedpark Fussgaengerbruecke"
        value_secret = "Substitution seit 2024, Hausarzt Dr. Muster"
        value_other = "Kurzkontakt am Automaten"

        _create_field_template(page, base_url, ft_plain, "text")
        _create_field_template(page, base_url, ft_secret, "textarea", encrypted=True)
        _create_field_template(page, base_url, ft_other, "text")

        pk_a = _create_entity_doctype(page, base_url, type_a, "client")
        _assign_free_field(page, ft_plain)
        _assign_free_field(page, ft_secret)
        pk_b = _create_entity_doctype(page, base_url, type_b, "client")
        _assign_free_field(page, ft_other)

        client_pk = None
        try:
            # --- Person anlegen -------------------------------------------------
            pseudonym = _cli_name("Person")
            page.goto(f"{base_url}/clients/new/", wait_until="domcontentloaded")

            # Abgrenzung: das Personen-Select zeigt NUR entity_type=client.
            select = page.locator(DOC_TYPE_SELECT)
            expect(select.locator("option", has_text=type_a)).to_have_count(1)
            expect(select.locator("option", has_text=type_b)).to_have_count(1)
            # Gegenprobe mit einem Seed-EREIGNIS-Typ (existiert, gehört aber nicht hierher).
            expect(select.locator("option", has_text="Krisengespräch")).to_have_count(0)

            page.fill('input[name="pseudonym"]', pseudonym)
            switch_document_type(page, label=type_a)
            fill_dynamic_field(page, f"input[name='{slug_plain}']", value_plain)
            fill_dynamic_field(page, f"textarea[name='{slug_secret}']", value_secret)
            page.locator(ENTITY_SUBMIT).click()
            page.wait_for_url(CLIENT_DETAIL_RE)
            client_pk = CLIENT_DETAIL_RE.search(page.url).group(1)
            edit_url = f"{base_url}/clients/{client_pk}/edit/"

            # --- Detail zeigt beide Werte im Klartext ---------------------------
            section = page.locator(DYNAMIC_SECTION)
            expect(section).to_be_visible(timeout=10000)
            expect(section).to_contain_text(type_a)
            expect(section).to_contain_text(ft_plain)
            expect(section).to_contain_text(value_plain)
            expect(section).to_contain_text(ft_secret)
            # Entschlüsselt: im JSONB steht ein Fernet-Token, die Anzeige den Klartext.
            expect(section).to_contain_text(value_secret)

            # --- Bearbeiten: Prefill + Änderung ---------------------------------
            page.goto(edit_url, wait_until="domcontentloaded")
            expect(page.locator(f"input[name='{slug_plain}']")).to_have_value(value_plain, timeout=10000)
            expect(page.locator(f"textarea[name='{slug_secret}']")).to_have_value(value_secret)
            expect(page.locator("[data-testid='doc-type-switch-hint']")).to_be_visible()

            page.fill(f"input[name='{slug_plain}']", value_plain_new)
            page.locator(ENTITY_SUBMIT).click()
            page.wait_for_url(CLIENT_DETAIL_RE)
            section = page.locator(DYNAMIC_SECTION)
            expect(section).to_contain_text(value_plain_new, timeout=10000)
            expect(section).not_to_contain_text(value_plain)
            # Der verschlüsselte Wert hat die Runde unverändert überstanden.
            expect(section).to_contain_text(value_secret)

            # --- Typwechsel verwirft die Altwerte (E11) -------------------------
            page.goto(edit_url, wait_until="domcontentloaded")
            expect(page.locator("[data-testid='doc-type-switch-hint']")).to_be_visible()
            switch_document_type(page, label=type_b)
            expect(page.locator(f"input[name='{slug_other}']")).to_be_visible(timeout=10000)
            # Das alte Feldset ist weg — nicht bloß unsichtbar.
            expect(page.locator(f"input[name='{slug_plain}']")).to_have_count(0)
            expect(page.locator(f"textarea[name='{slug_secret}']")).to_have_count(0)

            fill_dynamic_field(page, f"input[name='{slug_other}']", value_other)
            # Refs #1733: Der Wechsel verwirft zwei befüllte Werte — die
            # Rückfrage muss kommen (und die Anzahl nennen), sonst würde hier
            # unbemerkt gespeichert.
            dialogs = []
            page.once("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept()))
            page.locator(ENTITY_SUBMIT).click()
            page.wait_for_url(CLIENT_DETAIL_RE)
            assert dialogs, "Refs #1733: keine Rückfrage beim Typwechsel mit befüllten Werten"
            assert "2" in dialogs[0], f"Rückfrage ohne Feldanzahl: {dialogs[0]!r}"
            section = page.locator(DYNAMIC_SECTION)
            expect(section).to_contain_text(type_b, timeout=10000)
            expect(section).to_contain_text(value_other)
            # Beweiskraft: die Werte des alten Typs sind verworfen.
            expect(section).not_to_contain_text(value_plain_new)
            expect(section).not_to_contain_text(value_secret)

            # --- Abgrenzung: Personen-Typen tauchen im Ereignis-Picker nicht auf -
            page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
            event_select = page.locator(DOC_TYPE_SELECT)
            expect(event_select.locator("option", has_text=type_a)).to_have_count(0)
            expect(event_select.locator("option", has_text=type_b)).to_have_count(0)
            # Gegenprobe: der Filter ist nicht zu scharf, Ereignistypen bleiben wählbar.
            expect(event_select.locator("option", has_text="Krisengespräch")).to_have_count(1)

            assert errors == [], f"Konsolen-Errors/CSP-Verstöße im Personen-Fluss: {errors}"
        finally:
            if client_pk:
                _detach_document_type(page, base_url, f"{base_url}/clients/{client_pk}/edit/", CLIENT_DETAIL_RE)
            for pk in (pk_a, pk_b):
                _delete_doctype_safe(page, base_url, pk)
            for name in (ft_plain, ft_secret, ft_other):
                _delete_field_template(page, base_url, name)


# ---------------------------------------------------------------------------
# (2) Regression E-11/E-12: deaktivierter Ist-Typ
# ---------------------------------------------------------------------------


class TestInactiveAssignedClientType:
    """P7: der deaktivierte Ist-Typ übersteht Weg-und-Zurück im Select."""

    def test_inactive_assigned_type_survives_switch_away_and_back(self, authenticated_page, base_url):
        page = authenticated_page
        errors = track_console_errors(page)

        ft_own = _cli_name("wohnen")
        ft_away = _cli_name("sprache")
        slug_own, slug_away = _slug_of(ft_own), _slug_of(ft_away)
        type_own = _cli_name("TypIst")
        type_away = _cli_name("TypAlt")
        value_own = "WG-Platz im Nordviertel"

        _create_field_template(page, base_url, ft_own, "text")
        _create_field_template(page, base_url, ft_away, "text")
        pk_own = _create_entity_doctype(page, base_url, type_own, "client")
        _assign_free_field(page, ft_own)
        pk_away = _create_entity_doctype(page, base_url, type_away, "client")
        _assign_free_field(page, ft_away)

        client_pk = None
        try:
            # Person mit dem (noch aktiven) Ist-Typ anlegen.
            pseudonym = _cli_name("PersonInaktiv")
            page.goto(f"{base_url}/clients/new/", wait_until="domcontentloaded")
            page.fill('input[name="pseudonym"]', pseudonym)
            switch_document_type(page, label=type_own)
            fill_dynamic_field(page, f"input[name='{slug_own}']", value_own)
            page.locator(ENTITY_SUBMIT).click()
            page.wait_for_url(CLIENT_DETAIL_RE)
            client_pk = CLIENT_DETAIL_RE.search(page.url).group(1)
            edit_url = f"{base_url}/clients/{client_pk}/edit/"

            # Typ deaktivieren (Config-UI) — die Person behält ihn.
            _set_doctype_active(page, base_url, pk_own, False)

            page.goto(edit_url, wait_until="domcontentloaded")
            select = page.locator(DOC_TYPE_SELECT)
            # Der Ist-Typ bleibt vorausgewählt und ist als deaktiviert markiert
            # (E-10, Muster #1160) — ohne diese Ausnahme stünde hier
            # „— Kein Typ —" und ein normaler Submit löschte Typ + Werte.
            expect(select).to_have_value(pk_own)
            expect(select.locator("option", has_text=f"{type_own} (deaktiviert)")).to_have_count(1)
            expect(page.locator(f"input[name='{slug_own}']")).to_have_value(value_own, timeout=10000)

            # Weg auf einen anderen (aktiven) Typ …
            switch_document_type(page, label=type_away)
            expect(page.locator(f"input[name='{slug_away}']")).to_be_visible(timeout=10000)
            expect(page.locator(f"input[name='{slug_own}']")).to_have_count(0)

            # … und ZURÜCK auf den inaktiven Ist-Typ: das ist der Regressionsfall.
            # Vor dem Fix antwortete das Partial 404 (HTMX swapt bei 4xx nicht) —
            # ``switch_document_type`` prüft den Status deshalb explizit.
            switch_document_type(page, label=f"{type_own} (deaktiviert)")
            expect(page.locator(f"input[name='{slug_own}']")).to_be_visible(timeout=10000)
            expect(page.locator(f"input[name='{slug_away}']")).to_have_count(0)

            # Refs #1758: Der Rückweg auf den GESPEICHERTEN Typ ist kein
            # Typwechsel — das Feldset kommt mit den Bestandswerten zurück.
            # Vorher rendete das Partial hier leer, und der folgende Submit
            # überschrieb den Wert still mit einem Leerstring (der #1733-Confirm
            # warnte zwar, behob die Ursache aber nicht). Kein erneutes Eintippen
            # mehr: genau das ist der Regressionsschutz.
            expect(page.locator(f"input[name='{slug_own}']")).to_have_value(value_own, timeout=10000)
            # Refs #1733: Das Feldset wurde zweimal nachgeladen — die Rückfrage
            # hängt bewusst am Nachladen, nicht am Select-Wert, und kommt
            # deshalb auch bei diesem Weg-und-Zurück (genau hier drohte der
            # stille Verlust).
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator(ENTITY_SUBMIT).click()
            page.wait_for_url(CLIENT_DETAIL_RE)

            # Kein stiller Datenverlust: Typ UND Wert stehen weiter am Datensatz.
            section = page.locator(DYNAMIC_SECTION)
            expect(section).to_be_visible(timeout=10000)
            expect(section).to_contain_text(type_own)
            expect(section).to_contain_text(ft_own)
            expect(section).to_contain_text(value_own)

            assert errors == [], f"Konsolen-Errors/CSP-Verstöße im Inaktiv-Fluss: {errors}"
        finally:
            if client_pk:
                _detach_document_type(page, base_url, f"{base_url}/clients/{client_pk}/edit/", CLIENT_DETAIL_RE)
            for pk in (pk_own, pk_away):
                _delete_doctype_safe(page, base_url, pk)
            for name in (ft_own, ft_away):
                _delete_field_template(page, base_url, name)


# ---------------------------------------------------------------------------
# (3) Rückfrage vor dem Verwerfen von Zusatzfeld-Werten (Refs #1733)
# ---------------------------------------------------------------------------


def _wait_for_dialogs(page, dialogs, expected, *, attempts=50) -> None:
    """Python-seitig auf ``expected`` erfasste Dialoge pollen.

    Der Dialog-Handler laeuft asynchron zum ``click()`` — ohne Warteschleife
    liest die Assertion die Liste, bevor der Browser den Dialog ueberhaupt
    geoeffnet hat (kein ``wait_for_function``: dessen async-Falle liefert
    False-Positives, s. e2e-runbook).
    """
    for _ in range(attempts):
        if len(dialogs) >= expected:
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"Erwartet {expected} Rückfrage(n), erfasst: {dialogs}")


class TestTypeChangeDiscardConfirm:
    """Refs #1733: Der Typwechsel/-entfernen war die einzige irreversible
    Datenwirkung ohne Rückfrage. Geprüft wird beides — dass sie kommt, wenn
    etwas zu verlieren ist, und dass sie ausbleibt, wenn nicht."""

    def test_confirm_only_when_values_are_at_risk_and_dismiss_keeps_data(self, authenticated_page, base_url):
        page = authenticated_page
        errors = track_console_errors(page)

        ft_own = _cli_name("notiz")
        slug_own = _slug_of(ft_own)
        type_own = _cli_name("TypConfirm")
        value_own = "Erstkontakt am Bahnhofsvorplatz"

        _create_field_template(page, base_url, ft_own, "text")
        pk_own = _create_entity_doctype(page, base_url, type_own, "client")
        _assign_free_field(page, ft_own)

        # Ein einziger Dialog-Handler für den ganzen Fluss; ``mode`` schaltet
        # zwischen Bestätigen und Abbrechen um (mehrere ``page.once``-Handler
        # würden sich gegenseitig ins Gehege kommen).
        dialogs: list[str] = []
        mode = {"accept": True}

        def _handle(dialog):
            dialogs.append(dialog.message)
            dialog.accept() if mode["accept"] else dialog.dismiss()

        page.on("dialog", _handle)

        client_pk = None
        try:
            # --- Anlage: Feldset wird nachgeladen, aber nichts kann verloren gehen
            pseudonym = _cli_name("PersonConfirm")
            page.goto(f"{base_url}/clients/new/", wait_until="domcontentloaded")
            page.fill('input[name="pseudonym"]', pseudonym)
            switch_document_type(page, label=type_own)
            fill_dynamic_field(page, f"input[name='{slug_own}']", value_own)
            page.locator(ENTITY_SUBMIT).click()
            page.wait_for_url(CLIENT_DETAIL_RE)
            client_pk = CLIENT_DETAIL_RE.search(page.url).group(1)
            edit_url = f"{base_url}/clients/{client_pk}/edit/"
            assert dialogs == [], f"Anlage-Formular darf nicht nachfragen: {dialogs}"

            # --- Bearbeiten ohne Typwechsel: Warnung ja, Rückfrage nein --------
            page.goto(edit_url, wait_until="domcontentloaded")
            hint = page.locator("[data-testid='doc-type-switch-hint']")
            expect(hint).to_be_visible()
            expect(hint).to_contain_text("entfernt")
            page.fill('input[name="pseudonym"]', f"{pseudonym}-b")
            page.locator(ENTITY_SUBMIT).click()
            page.wait_for_url(CLIENT_DETAIL_RE)
            assert dialogs == [], f"Speichern ohne Typwechsel darf nicht nachfragen: {dialogs}"

            # --- Typ entfernen + Abbrechen: kein Submit, Formular bleibt bedienbar
            mode["accept"] = False
            page.goto(edit_url, wait_until="domcontentloaded")
            switch_document_type(page, value="")
            submit = page.locator(ENTITY_SUBMIT)
            submit.click()
            _wait_for_dialogs(page, dialogs, 1)
            assert "1" in dialogs[0], f"Rückfrage ohne Feldanzahl: {dialogs[0]!r}"
            # Abbruch heisst: kein Submit — wir stehen weiter im Formular.
            expect(page).to_have_url(re.compile(r"/clients/[0-9a-f-]+/edit/$"))

            # Regressionsschutz (Review #1755): Der Doppel-Submit-Guard
            # (double-submit.js) läuft in der Capture-Phase VOR dem Alpine-
            # ``@submit="confirmDiscard"`` (Target-Phase) und plante das Disablen
            # der Submit-Buttons per setTimeout. Ohne die ``defaultPrevented``-
            # Prüfung im Guard bliebe der Speichern-Button nach dem Abbrechen
            # dauerhaft disabled und ``data-submitting`` scharf — jeder weitere
            # Submit geblockt, ungespeicherte Eingaben beim Reload verloren.
            # Deshalb OHNE Neuladen der Seite prüfen, dass der Button wieder
            # bedienbar ist ...
            expect(submit).to_be_enabled()

            # --- ... und dass ein erneutes Abschicken (diesmal bestätigt) auf
            # demselben, weiter bedienten Formular tatsächlich speichert und den
            # Altwert verwirft. Kein ``page.goto`` dazwischen — eine Navigation
            # würde die Sperre ohnehin zurücksetzen und den Bug maskieren.
            mode["accept"] = True
            submit.click()
            page.wait_for_url(CLIENT_DETAIL_RE)
            _wait_for_dialogs(page, dialogs, 2)
            expect(page.locator("#main-content")).not_to_contain_text(value_own)

            assert errors == [], f"Konsolen-Errors/CSP-Verstöße im Confirm-Fluss: {errors}"
        finally:
            page.remove_listener("dialog", _handle)
            if client_pk:
                _detach_document_type(page, base_url, f"{base_url}/clients/{client_pk}/edit/", CLIENT_DETAIL_RE)
            _delete_doctype_safe(page, base_url, pk_own)
            _delete_field_template(page, base_url, ft_own)
