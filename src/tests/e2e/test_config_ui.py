"""E2E-Tests für die Formular-Konfigurations-UI (M0 P9, Refs #1056).

Deckt die Config-CRUD-Flächen unter ``/config/`` ab: Erreichbarkeit + CSP-
Konsolen-Check, Rollen-Sperre (403), DocumentType- und FieldTemplate-CRUD,
das interaktive Feld-Board (Gruppen/Feld-Zuordnung) sowie die Drag-and-Drop-
Sortierung (Gruppen-Reorder + Cross-Gruppen-Feld-Move).

Wegwerf-Konvention (E2E-DB ist geteilt!): Alle in diesen Tests erzeugten Objekte
tragen das Präfix ``E2E-CFG-…`` und werden in ``finally`` wieder gelöscht. Die
**Seed-Gruppen/-Zuordnungen** des Kontakt-Typs werden NIE mutiert — das Feld-
Board wird ausschließlich an eigens angelegten Wegwerf-Dokumentationstypen
getestet (``test_event_field_groups.py`` und andere Suiten erwarten den
Seed-Zustand unverändert).
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from ._helpers import enter_sudo_mode, track_console_errors

pytestmark = pytest.mark.e2e

DOCTYPE_LIST = "/config/document-types/"
FIELDTEMPLATE_LIST = "/config/field-templates/"

# Submit-Button des jeweiligen Full-Page-Formulars eindeutig über das
# name-Feld scopen (nie ein nackter button[type=submit] — der matcht auch
# Navigations-/Logout-/Sprach-Buttons, siehe e2e-runbook § 4).
FORM_SUBMIT = "form:has(#id_name) button[type='submit']"

_DETAIL_RE = re.compile(r"/config/document-types/([0-9a-f-]+)/$")


def _cfg_name(kind: str) -> str:
    """Eindeutiger Wegwerf-Name mit ``E2E-CFG``-Präfix."""
    return f"E2E-CFG-{kind}-{uuid.uuid4().hex[:6]}"


class TestFacilitySettingsHub:
    """Der neue Hub-Reiter nutzt Sudo, Gruppen und den Audit-Service (#1885)."""

    def test_admin_updates_settings_and_sees_minimized_audit(self, authenticated_page, base_url):
        page = authenticated_page
        console_errors = track_console_errors(page)
        enter_sudo_mode(page, base_url)

        page.goto(f"{base_url}/verwaltung/", wait_until="domcontentloaded")
        # #1871 ergaenzt den Zugaenge-Reiter zum bestehenden Settings-Hub.
        expect(page.locator('[data-testid^="subnav-tab-"]')).to_have_count(6)
        warning_links = page.locator('[data-testid="settings-warnings-list"] a[href="/verwaltung/einstellungen/"]')
        expect(warning_links).to_have_count(2)

        page.goto(f"{base_url}/verwaltung/einstellungen/", wait_until="domcontentloaded")
        # #2136 ergaenzt die Sektion Quick-Exit/Sichtschutz (Gruppe
        # "Mitschauen-Schutz" in core/forms/facility_settings.py) als fuenfte
        # Gruppe; das Formular rendert ein fieldset je Gruppe. Weiterhin exakt
        # gezaehlt: die Zahl haelt fest, dass keine Gruppe still verschwindet
        # oder unbemerkt dazukommt.
        expect(page.locator("fieldset")).to_have_count(5)
        expect(page.locator('input[name="facility_full_name"]')).to_be_visible()
        expect(page.locator('input[name="max_file_size_mb"]')).to_be_visible()

        name_input = page.locator('input[name="facility_full_name"]')
        size_input = page.locator('input[name="max_file_size_mb"]')
        original_name = name_input.input_value()
        original_size = size_input.input_value()
        changed_name = _cfg_name("SETTINGS")
        submit = page.locator('form:has(input[name="expected_updated_at"]) button[type="submit"]')

        try:
            name_input.fill(changed_name)
            size_input.fill("11" if original_size != "11" else "12")
            submit.click()
            page.wait_for_url(re.compile(r"/verwaltung/einstellungen/$"), timeout=10000)
            expect(page.get_by_text("Einstellungen wurden gespeichert.")).to_be_visible()
            expect(page.locator('input[name="facility_full_name"]')).to_have_value(changed_name)

            page.goto(f"{base_url}/audit/", wait_until="domcontentloaded")
            audit_row = page.locator("table tbody tr").filter(has_text="Einstellungen geändert").first
            expect(audit_row).to_be_visible()
            expect(audit_row).to_contain_text("facility_full_name")
            expect(audit_row).to_contain_text("max_file_size_mb")
            expect(audit_row).not_to_contain_text(changed_name)
            assert console_errors == []
        finally:
            # Die E2E-DB ist suite-weit geteilt: den Seed-Settings-Stand fuer
            # nachfolgende Tests wiederherstellen. Die Audit-Spur bleibt als
            # korrekter Nachweis beider echten UI-Mutationen bestehen.
            page.goto(f"{base_url}/verwaltung/einstellungen/", wait_until="domcontentloaded")
            page.locator('input[name="facility_full_name"]').fill(original_name)
            page.locator('input[name="max_file_size_mb"]').fill(original_size)
            page.locator('form:has(input[name="expected_updated_at"]) button[type="submit"]').click()
            page.wait_for_url(re.compile(r"/verwaltung/einstellungen/$"), timeout=10000)


# ---------------------------------------------------------------------------
# UI-Helfer (alle mandantengescoped über die eingeloggte Session)
# ---------------------------------------------------------------------------


def _create_doctype(page, base_url, name, system_type_value="") -> str:
    """Legt einen Wegwerf-Dokumentationstyp an; gibt dessen UUID (aus der URL) zurück.

    ``system_type`` wird bewusst mitbedient (nur beim Anlegen editierbar, bei
    der Bearbeitung serverseitig unveränderbar/disabled). Der Default ist seit
    E4 (Refs #1850) die **Leer-Auswahl**: das Enum kennt nur noch ``ban`` und
    ``crisis``, und beide tragen App-Verhalten, das ein Wegwerf-Typ nicht
    ungefragt bekommen soll (``crisis`` würde zusätzlich den Berichtsschlüssel
    vorbelegen).
    """
    page.goto(f"{base_url}{DOCTYPE_LIST}new/", wait_until="domcontentloaded")
    page.fill("input#id_name", name)
    page.select_option("select#id_system_type", value=system_type_value)
    page.locator(FORM_SUBMIT).click()
    page.wait_for_url(_DETAIL_RE)
    match = _DETAIL_RE.search(page.url)
    assert match, f"Detail-URL nicht erkannt: {page.url}"
    return match.group(1)


def _delete_doctype(page, base_url, pk) -> None:
    """Best-effort-Cleanup: Wegwerf-Dokumentationstyp löschen (ignoriert Fehler)."""
    try:
        page.goto(f"{base_url}{DOCTYPE_LIST}{pk}/delete/", wait_until="domcontentloaded")
        confirm = page.locator('[data-testid="doctype-delete-confirm"]')
        if confirm.count() == 0:
            return
        confirm.click()
        page.wait_for_url(re.compile(r"/config/document-types/$"), timeout=10000)
    except Exception:
        # Cleanup ist best-effort — ein bereits gelöschter/kaputter Zustand darf
        # den Test-Abschluss nicht sprengen.
        pass


def _add_group(page, name) -> str:
    """Legt über das Board-„Neue Gruppe"-Formular eine Gruppe an; gibt deren pk zurück."""
    form = page.locator('form[data-testid="group-form"]:has(#id_group-new-name)')
    form.locator("#id_group-new-name").fill(name)
    form.locator("button[type='submit']").click()
    card = page.locator(f'[data-testid="field-group"]:has(h3:has-text("{name}"))')
    expect(card).to_be_visible(timeout=10000)
    return card.get_attribute("data-group-id")


def _assign_field(page, template_text, group_text) -> str:
    """Ordnet eine (bestehende Seed-)Feldvorlage per Dropdown in eine Gruppe zu.

    Auswahl über den Options-Value (die Option-Labels tragen den
    ``FieldTemplate.__str__``-/``FieldGroup.__str__``-Präfix, ein exaktes
    ``select_option(label=…)`` würde daher nicht greifen). Gibt die
    ``data-dtf-id`` der neu erzeugten Board-Zeile zurück.
    """
    form = page.locator('[data-testid="field-add-form"]')
    ft_val = form.locator('select[name="field_template"] option', has_text=template_text).first.get_attribute("value")
    assert ft_val, f"Feldvorlage-Option „{template_text}“ nicht gefunden"
    form.locator('select[name="field_template"]').select_option(value=ft_val)
    fg_val = form.locator('select[name="field_group"] option', has_text=group_text).first.get_attribute("value")
    assert fg_val, f"Feldgruppen-Option „{group_text}“ nicht gefunden"
    form.locator('select[name="field_group"]').select_option(value=fg_val)
    form.locator("button[type='submit']").click()
    row = page.locator(f'[data-testid="field-row"]:has-text("{template_text}")')
    expect(row).to_be_visible(timeout=10000)
    return row.get_attribute("data-dtf-id")


def _assign_free_field(page, template_text) -> str:
    """Ordnet eine Feldvorlage OHNE Gruppe zu (freier Top-Level-Block, #1713).

    Wählt die Leeroption „– Ohne Gruppe –" (value="") im Feldgruppen-Dropdown und
    gibt die ``data-dtf-id`` der neuen Top-Level-Zeile zurück.
    """
    form = page.locator('[data-testid="field-add-form"]')
    ft_val = form.locator('select[name="field_template"] option', has_text=template_text).first.get_attribute("value")
    assert ft_val, f"Feldvorlage-Option „{template_text}“ nicht gefunden"
    form.locator('select[name="field_template"]').select_option(value=ft_val)
    form.locator('select[name="field_group"]').select_option(value="")
    form.locator("button[type='submit']").click()
    row = page.locator(f'[data-testid="free-field-row"]:has-text("{template_text}")')
    expect(row).to_be_visible(timeout=10000)
    return row.get_attribute("data-dtf-id")


def _block_order(page) -> list[str]:
    """DOM-Reihenfolge der Top-Level-Block-Kennungen (#1713): Gruppen-pk bzw.
    ``dtf:<pk>`` für freie Felder — die persistierte Ansicht der Block-Sequenz."""
    blocks = page.locator('[data-sortable="blocks"] > [data-block]')
    order = []
    for i in range(blocks.count()):
        block = blocks.nth(i)
        gid = block.get_attribute("data-group-id")
        order.append(gid if gid else f"dtf:{block.get_attribute('data-dtf-id')}")
    return order


def _group_order(page) -> list[str]:
    """DOM-Reihenfolge NUR der Gruppen-pks im Blocks-Container (persistierte Ansicht)."""
    cards = page.locator('[data-sortable="blocks"] > [data-block][data-group-id]')
    return [cards.nth(i).get_attribute("data-group-id") for i in range(cards.count())]


# ---------------------------------------------------------------------------
# Erreichbarkeit + CSP-Konsolen-Check (Issue-Verifikationspunkt)
# ---------------------------------------------------------------------------


class TestConfigReachableNoConsoleErrors:
    """Config-Flächen sind für die Anwendungsbetreuung erreichbar und CSP-sauber."""

    def test_config_pages_have_no_console_errors(self, authenticated_page, base_url):
        """Liste (DocType + FieldTemplate) und eine Detail-Seite: 0 Konsolen-Errors.

        CSP-Violations tauchen als Konsolen-``error`` auf — der Verifikationspunkt
        aus [#1056] für „keine CSP-Verstöße" auf der neuen Config-UI.
        """
        page = authenticated_page
        errors = track_console_errors(page)

        for path in (DOCTYPE_LIST, FIELDTEMPLATE_LIST):
            page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
            page.wait_for_load_state("domcontentloaded")

        # Detail-Seite eines Seed-Typs (rendert das volle Feld-Board inkl.
        # SortableJS + config-sortable.js — der CSP-kritischste Pfad).
        page.goto(f"{base_url}{DOCTYPE_LIST}", wait_until="domcontentloaded")
        # Deterministisch den Seed-Typ „Kontakt" öffnen (Selector-Stability-Guard:
        # kein first-Klick — Seed-Drift/Wegwerf-Typen ändern die Reihenfolge).
        page.locator('[data-testid="doctype-detail-link"]').filter(has_text="Kontakt").click()
        page.wait_for_url(_DETAIL_RE)
        page.wait_for_load_state("domcontentloaded")
        # Board-Init (defer-Skripte) kurz settlen lassen, damit ein etwaiger
        # CSP-/JS-Fehler auch wirklich in der Konsole landet.
        page.wait_for_timeout(700)

        assert errors == [], f"Konsolen-Errors/CSP-Verstöße auf der Config-UI: {errors}"


# ---------------------------------------------------------------------------
# Rollen-Sperre: nur facility_admin (403 für lead/staff/assistant)
# ---------------------------------------------------------------------------


class TestConfigRolePermissions:
    """``/config/`` ist ausschließlich für die Anwendungsbetreuung (facility_admin)."""

    def _assert_forbidden(self, page, base_url):
        resp = page.goto(f"{base_url}{DOCTYPE_LIST}", wait_until="domcontentloaded")
        assert resp.status == 403, f"Erwartet 403, bekam {resp.status} auf {page.url}"

    def test_lead_forbidden(self, lead_page, base_url):
        self._assert_forbidden(lead_page, base_url)

    def test_staff_forbidden(self, staff_page, base_url):
        self._assert_forbidden(staff_page, base_url)

    def test_assistant_forbidden(self, assistant_page, base_url):
        self._assert_forbidden(assistant_page, base_url)


# ---------------------------------------------------------------------------
# DocumentType-CRUD-Roundtrip
# ---------------------------------------------------------------------------


class TestDocumentTypeCrud:
    """Anlegen → Liste → Bearbeiten (system_type disabled) → Löschen."""

    def test_doctype_crud_roundtrip(self, authenticated_page, base_url):
        page = authenticated_page
        name = _cfg_name("DT")
        pk = _create_doctype(page, base_url, name)
        new_name = f"{name}-edit"
        try:
            # In der Liste (per Suche) sichtbar.
            page.goto(f"{base_url}{DOCTYPE_LIST}?q={name}", wait_until="domcontentloaded")
            expect(page.locator(f'[data-testid="doctype-row"]:has-text("{name}")')).to_have_count(1, timeout=10000)

            # Bearbeiten: system_type-Select ist disabled, Name ändern.
            page.goto(f"{base_url}{DOCTYPE_LIST}{pk}/edit/", wait_until="domcontentloaded")
            expect(page.locator("select#id_system_type")).to_be_disabled()
            page.fill("input#id_name", new_name)
            page.locator(FORM_SUBMIT).click()
            page.wait_for_url(re.compile(rf"/config/document-types/{pk}/$"))
            expect(page.locator("h1")).to_contain_text(new_name)

            # Löschen über Detail → Confirm → POST.
            page.locator('[data-testid="doctype-delete-link"]').click()
            page.wait_for_url(re.compile(rf"/config/document-types/{pk}/delete/$"))
            page.locator('[data-testid="doctype-delete-confirm"]').click()
            page.wait_for_url(re.compile(r"/config/document-types/$"))

            # Weg: Suche liefert keine Zeile mehr.
            page.goto(f"{base_url}{DOCTYPE_LIST}?q={new_name}", wait_until="domcontentloaded")
            expect(page.locator(f'[data-testid="doctype-row"]:has-text("{new_name}")')).to_have_count(0, timeout=10000)
        finally:
            _delete_doctype(page, base_url, pk)

    def test_icon_color_and_sort_order_roundtrip(self, authenticated_page, base_url):
        """Manuell zuerst auf Port 8844 gepruefter Darstellungs-Workflow (#1886)."""
        page = authenticated_page
        errors = track_console_errors(page)
        name = _cfg_name("DT-PRESENTATION")
        pk = None

        try:
            page.goto(f"{base_url}{DOCTYPE_LIST}new/", wait_until="domcontentloaded")
            expect(page.locator("input#id_icon")).to_have_attribute("type", "text")
            expect(page.locator("input#id_color")).to_have_attribute("type", "text")
            expect(page.locator("input#id_sort_order")).to_have_attribute("type", "number")
            expect(page.locator("form:has(#id_name)")).to_contain_text("Kontrast")

            page.fill("input#id_name", name)
            page.fill("input#id_icon", "heart")
            page.fill("input#id_color", "rose")
            page.fill("input#id_sort_order", "17")
            page.locator(FORM_SUBMIT).click()
            page.wait_for_url(_DETAIL_RE)
            match = _DETAIL_RE.search(page.url)
            assert match, f"Detail-URL nicht erkannt: {page.url}"
            pk = match.group(1)

            page.locator('[data-testid="doctype-edit-link"]').click()
            page.wait_for_url(re.compile(rf"/config/document-types/{pk}/edit/$"))
            expect(page.locator("input#id_icon")).to_have_value("heart")
            expect(page.locator("input#id_color")).to_have_value("rose")
            expect(page.locator("input#id_sort_order")).to_have_value("17")

            page.fill("input#id_icon", "map-pin")
            page.fill("input#id_color", "blue")
            page.fill("input#id_sort_order", "2")
            page.locator(FORM_SUBMIT).click()
            page.wait_for_url(re.compile(rf"/config/document-types/{pk}/$"))
            page.locator('[data-testid="doctype-edit-link"]').click()
            expect(page.locator("input#id_icon")).to_have_value("map-pin")
            expect(page.locator("input#id_color")).to_have_value("blue")
            expect(page.locator("input#id_sort_order")).to_have_value("2")
            assert errors == [], f"Konsolen-Errors im Darstellungsformular: {errors}"
        finally:
            if pk:
                _delete_doctype(page, base_url, pk)

    def test_tally_opt_in_controls_zeitstrom_visibility(self, authenticated_page, staff_page, base_url):
        """Die fachliche Config-UI schaltet einen geeigneten Typ in der Strichliste ein und aus."""
        admin = authenticated_page
        admin_errors = track_console_errors(admin)
        staff_errors = track_console_errors(staff_page)
        name = _cfg_name("Tally")
        pk = None
        try:
            admin.goto(f"{base_url}{DOCTYPE_LIST}new/", wait_until="domcontentloaded")
            admin.fill("input#id_name", name)
            tally = admin.locator("input#id_tally_enabled")
            expect(tally).to_be_visible()
            expect(tally).not_to_be_checked()
            tally.check()
            admin.locator(FORM_SUBMIT).click()
            admin.wait_for_url(_DETAIL_RE)
            match = _DETAIL_RE.search(admin.url)
            assert match, f"Detail-URL nicht erkannt: {admin.url}"
            pk = match.group(1)
            expect(admin.locator('[data-testid="doctype-tally-status"]')).to_contain_text("Angeboten")

            staff_page.goto(f"{base_url}/", wait_until="domcontentloaded")
            tally_row = staff_page.locator(
                '[data-testid="tally-panel"] [data-testid^="tally-row-"]',
                has_text=name,
            )
            expect(tally_row).to_have_count(1)
            expect(tally_row.locator('[data-testid^="tally-count-"]')).to_have_text("0")

            admin.goto(f"{base_url}{DOCTYPE_LIST}{pk}/edit/", wait_until="domcontentloaded")
            tally = admin.locator("input#id_tally_enabled")
            expect(tally).to_be_checked()
            tally.uncheck()
            admin.locator(FORM_SUBMIT).click()
            admin.wait_for_url(re.compile(rf"/config/document-types/{pk}/$"))
            expect(admin.locator('[data-testid="doctype-tally-status"]')).to_contain_text("Nicht angeboten")

            staff_page.reload(wait_until="domcontentloaded")
            expect(
                staff_page.locator(
                    '[data-testid="tally-panel"] [data-testid^="tally-row-"]',
                    has_text=name,
                )
            ).to_have_count(0)
            assert admin_errors == [], f"Konsolen-Errors in der Dokumentationstyp-Konfiguration: {admin_errors}"
            assert staff_errors == [], f"Konsolen-Errors in der Strichliste: {staff_errors}"
        finally:
            if pk:
                _delete_doctype(admin, base_url, pk)


# ---------------------------------------------------------------------------
# Entitätstyp-abhängige Feld-Sichtbarkeit (Refs #1757, Option C aus #1731)
# ---------------------------------------------------------------------------


class TestDocumentTypeEntityScopedFields:
    """Nur für Ereignisse wirksame Felder folgen dem Entitätstyp.

    Die drei Felder wirken ausschließlich an ``entity_type=event``-Typen. Das
    Formular blendet sie deshalb aus, sobald „Person" oder „Fall" gewählt ist —
    **rein visuell**: die Eingabefelder bleiben im DOM und aktiviert, ihre Werte
    werden unverändert mitgepostet. Genau das ist der kritische Teil: fiele ein
    ausgeblendetes Feld aus dem POST, landete es auf dem Modell-Default (bei
    ``sensitivity`` wäre das ein stiller Absturz der Zugriffsschranke;
    ``sensitivity`` wird deshalb nie ausgeblendet).
    """

    def test_scoped_fields_toggle_and_values_survive_saving(self, authenticated_page, base_url):
        page = authenticated_page
        errors = track_console_errors(page)
        name = _cfg_name("Scope")
        pk = None
        try:
            page.goto(f"{base_url}{DOCTYPE_LIST}new/", wait_until="domcontentloaded")
            page.fill("input#id_name", name)

            # Ausgangszustand ist „Ereignis" (Modell-Default) — alles sichtbar.
            expect(page.locator("select#id_entity_type")).to_have_value("event")
            expect(page.locator("select#id_system_type")).to_be_visible(timeout=10000)
            expect(page.locator("select#id_min_contact_stage")).to_be_visible()
            expect(page.locator("input#id_retention_days")).to_be_visible()
            expect(page.locator("input#id_tally_enabled")).to_be_visible()

            # Werte setzen, SOLANGE die Felder sichtbar sind.
            page.select_option("select#id_sensitivity", value="elevated")
            page.fill("input#id_retention_days", "30")

            # Auf „Person" umstellen: die drei Felder verschwinden …
            page.select_option("select#id_entity_type", value="client")
            expect(page.locator("select#id_system_type")).to_be_hidden(timeout=10000)
            expect(page.locator("select#id_min_contact_stage")).to_be_hidden()
            expect(page.locator("input#id_retention_days")).to_be_hidden()
            expect(page.locator("input#id_tally_enabled")).to_be_hidden()
            # … alles Übrige bleibt stehen (``sensitivity`` steuert den Zugriff).
            expect(page.locator("input#id_name")).to_be_visible()
            expect(page.locator("select#id_entity_type")).to_be_visible()
            expect(page.locator("select#id_sensitivity")).to_be_visible()
            expect(page.locator("textarea#id_description")).to_be_visible()

            # „Fall" verhält sich gleich, „Ereignis" holt die Felder zurück —
            # samt unverändertem Wert (nichts wurde beim Ausblenden geleert).
            page.select_option("select#id_entity_type", value="case")
            expect(page.locator("input#id_retention_days")).to_be_hidden(timeout=10000)
            page.select_option("select#id_entity_type", value="event")
            expect(page.locator("input#id_retention_days")).to_be_visible(timeout=10000)
            expect(page.locator("input#id_tally_enabled")).to_be_visible()
            expect(page.locator("input#id_retention_days")).to_have_value("30")

            # Speichern MIT ausgeblendeten Feldern als „Person": ``sensitivity``
            # überlebt (steuert den Zugriff, gilt für jeden Entitätstyp),
            # ``retention_days`` wird bewusst geleert — die Frist wirkt nur an
            # Ereignis-Typen (ADR-039 Event-only-Doktrin), und der Server NULLt
            # sie beim Speichern für Personen-/Fall-Typen (Refs #1776).
            page.select_option("select#id_entity_type", value="client")
            expect(page.locator("input#id_retention_days")).to_be_hidden(timeout=10000)
            page.locator(FORM_SUBMIT).click()
            page.wait_for_url(_DETAIL_RE)
            match = _DETAIL_RE.search(page.url)
            assert match, f"Detail-URL nicht erkannt: {page.url}"
            pk = match.group(1)

            # Gegenprobe am Bearbeiten-Formular: ``sensitivity`` steht am Datensatz,
            # ``retention_days`` ist leer (für „Person" serverseitig genullt), der
            # Entitätstyp ist fest, die Felder bleiben ausgeblendet.
            page.goto(f"{base_url}{DOCTYPE_LIST}{pk}/edit/", wait_until="domcontentloaded")
            expect(page.locator("select#id_sensitivity")).to_have_value("elevated", timeout=10000)
            expect(page.locator("input#id_retention_days")).to_have_value("")
            expect(page.locator("select#id_entity_type")).to_be_disabled()
            expect(page.locator("input#id_retention_days")).to_be_hidden()
            expect(page.locator("select#id_sensitivity")).to_be_visible()

            assert errors == [], f"Konsolen-Errors/CSP-Verstöße im Entitätstyp-Formular: {errors}"
        finally:
            if pk:
                _delete_doctype(page, base_url, pk)


# ---------------------------------------------------------------------------
# FieldTemplate-CRUD + Toggle + Löschschutz
# ---------------------------------------------------------------------------


class TestFieldTemplateCrud:
    """Anlegen (Slug) → Deaktivieren (Toggle) → Löschen; Seed-„Dauer" ist geschützt."""

    def _create_field_template(self, page, base_url, name):
        page.goto(f"{base_url}{FIELDTEMPLATE_LIST}new/", wait_until="domcontentloaded")
        page.fill("input#id_name", name)
        page.select_option("select#id_field_type", value="text")
        page.locator(FORM_SUBMIT).click()
        page.wait_for_url(re.compile(r"/config/field-templates/$"))

    def _row(self, page, name):
        return page.locator(f'[data-testid="fieldtemplate-row"]:has-text("{name}")')

    def test_fieldtemplate_create_toggle_delete(self, authenticated_page, base_url):
        page = authenticated_page
        name = _cfg_name("FT")
        expected_slug = name.lower()  # keine Umlaute → slugify == lower()
        created = False
        try:
            self._create_field_template(page, base_url, name)
            created = True

            # Slug erscheint in der Liste.
            page.goto(f"{base_url}{FIELDTEMPLATE_LIST}?q={name}", wait_until="domcontentloaded")
            row = self._row(page, name)
            expect(row).to_have_count(1, timeout=10000)
            expect(row).to_contain_text(expected_slug)
            expect(row).to_contain_text("Aktiv")

            # Deaktivieren (Toggle) → Status Inaktiv.
            row.locator('[data-testid="fieldtemplate-toggle-btn"]').click()
            page.wait_for_url(re.compile(r"/config/field-templates/$"))
            page.goto(f"{base_url}{FIELDTEMPLATE_LIST}?q={name}", wait_until="domcontentloaded")
            expect(self._row(page, name)).to_contain_text("Inaktiv", timeout=10000)

            # Löschen (kein Blocker) → weg.
            self._row(page, name).locator('[data-testid="fieldtemplate-delete-link"]').click()
            page.wait_for_url(re.compile(r"/config/field-templates/[0-9a-f-]+/delete/$"))
            expect(page.locator('[data-testid="fieldtemplate-delete-blockers"]')).to_have_count(0)
            page.locator('[data-testid="fieldtemplate-delete-confirm"]').click()
            page.wait_for_url(re.compile(r"/config/field-templates/$"))
            created = False

            page.goto(f"{base_url}{FIELDTEMPLATE_LIST}?q={name}", wait_until="domcontentloaded")
            expect(self._row(page, name)).to_have_count(0, timeout=10000)
        finally:
            if created:
                # Best-effort: verwaiste Wegwerf-Vorlage entfernen (nur falls der
                # Test vor dem Löschen abbrach).
                try:
                    page.goto(f"{base_url}{FIELDTEMPLATE_LIST}?q={name}", wait_until="domcontentloaded")
                    link = self._row(page, name).locator('[data-testid="fieldtemplate-delete-link"]')
                    if link.count():
                        link.click()
                        page.wait_for_url(re.compile(r"/config/field-templates/[0-9a-f-]+/delete/$"))
                        btn = page.locator('[data-testid="fieldtemplate-delete-confirm"]')
                        if btn.is_enabled():
                            btn.click()
                except Exception:
                    pass

    def test_select_options_editor_roundtrip_and_expert_validation(self, authenticated_page, base_url):
        """Zeileneditor → Persistenz → Expertinnen-Fallback (#1875).

        Der Ablauf wurde vor diesem E2E-Test manuell auf Port 8844 geprüft.
        """
        page = authenticated_page
        errors = track_console_errors(page)
        name = _cfg_name("FT-OPTIONS")
        created = False

        try:
            page.goto(f"{base_url}{FIELDTEMPLATE_LIST}new/", wait_until="domcontentloaded")
            expect(page.locator("textarea#id_options_json")).to_be_visible()
            expect(page.locator('[data-testid="options-editor"]')).to_be_hidden()
            assert "Progressive Enhancement fuer FieldTemplate.options_json" not in page.locator("body").inner_text()

            page.fill("input#id_name", name)
            page.select_option("select#id_field_type", value="select")
            expect(page.locator('[data-testid="options-editor"]')).to_be_visible()
            expect(page.locator("textarea#id_options_json")).to_be_hidden()

            page.locator("[data-options-add]").click()
            row = page.locator("[data-option-row]").first
            row.locator("[data-option-label]").fill("Akutberatung")
            row.locator("[data-option-slug]").fill("akutberatung")
            raw_options = page.locator("textarea#id_options_json").input_value()
            assert '"slug": "akutberatung"' in raw_options

            page.locator(FORM_SUBMIT).click()
            page.wait_for_url(re.compile(r"/config/field-templates/$"))
            created = True

            page.goto(f"{base_url}{FIELDTEMPLATE_LIST}?q={name}", wait_until="domcontentloaded")
            self._row(page, name).locator('[data-testid="fieldtemplate-edit-link"]').click()
            page.wait_for_url(re.compile(r"/config/field-templates/[0-9a-f-]+/edit/$"))
            expect(page.locator("[data-option-row]")).to_have_count(1)
            expect(page.locator("[data-option-label]")).to_have_value("Akutberatung")
            expect(page.locator("[data-option-slug]")).to_have_value("akutberatung")

            page.locator('[data-testid="options-expert-toggle"]').click()
            expect(page.locator("textarea#id_options_json")).to_be_visible()
            page.locator("textarea#id_options_json").fill("{kaputt")
            page.locator('[data-testid="options-expert-toggle"]').click()
            expect(page.locator("[data-options-json-error]")).to_be_visible()
            expect(page.locator("textarea#id_options_json")).to_be_visible()

            assert errors == [], f"Konsolen-Errors im Auswahloptionen-Editor: {errors}"
        finally:
            if created:
                try:
                    page.goto(f"{base_url}{FIELDTEMPLATE_LIST}?q={name}", wait_until="domcontentloaded")
                    link = self._row(page, name).locator('[data-testid="fieldtemplate-delete-link"]')
                    if link.count():
                        link.click()
                        page.wait_for_url(re.compile(r"/config/field-templates/[0-9a-f-]+/delete/$"))
                        page.locator('[data-testid="fieldtemplate-delete-confirm"]').click()
                except Exception:
                    pass

    def test_seed_template_delete_is_blocked(self, authenticated_page, base_url):
        """Seed-Vorlage „Dauer" ist in mehreren Typen zugeordnet → Löschen blockiert.

        Blocker-Seite zeigt Gründe, Löschbutton ist disabled — Abbruch, es wird
        NICHTS gelöscht (Seed-Zustand bleibt unberührt).
        """
        page = authenticated_page
        page.goto(f"{base_url}{FIELDTEMPLATE_LIST}?q=Dauer", wait_until="domcontentloaded")
        row = page.locator('[data-testid="fieldtemplate-row"]:has-text("Dauer")').first
        expect(row).to_be_visible(timeout=10000)
        row.locator('[data-testid="fieldtemplate-delete-link"]').click()
        page.wait_for_url(re.compile(r"/config/field-templates/[0-9a-f-]+/delete/$"))

        expect(page.locator('[data-testid="fieldtemplate-delete-blockers"]')).to_be_visible()
        expect(page.locator('[data-testid="fieldtemplate-delete-confirm"]')).to_be_disabled()
        # Abbruch: keine Mutation.


# ---------------------------------------------------------------------------
# Feld-Board am Wegwerf-DocType (Seed bleibt unberührt)
# ---------------------------------------------------------------------------


class TestFieldBoard:
    """Gruppe anlegen → Feld zuordnen → Gruppe umbenennen → Feld entfernen → Gruppe löschen."""

    def test_field_board_group_and_field_lifecycle(self, authenticated_page, base_url):
        page = authenticated_page
        pk = _create_doctype(page, base_url, _cfg_name("DT-BOARD"))
        try:
            group = _cfg_name("GRP")
            gpk = _add_group(page, group)

            # Bestehende Seed-Feldvorlage „Dauer" in die neue Gruppe zuordnen.
            _assign_field(page, "Dauer", group)
            card = page.locator(f'[data-testid="field-group"][data-group-id="{gpk}"]')
            expect(card.locator('[data-testid="field-row"]:has-text("Dauer")')).to_have_count(1, timeout=10000)

            # Gruppe umbenennen (Inline-Edit).
            renamed = _cfg_name("GRP2")
            card.locator('[data-testid="group-edit-btn"]').click()
            edit_input = page.locator(f"#id_group-{gpk}-name")
            expect(edit_input).to_be_visible(timeout=10000)
            edit_input.fill(renamed)
            page.locator(f'form[data-testid="group-form"]:has(#id_group-{gpk}-name) button[type="submit"]').click()
            card = page.locator(f'[data-testid="field-group"][data-group-id="{gpk}"]')
            expect(card.locator("h3")).to_contain_text(renamed, timeout=10000)

            # Feld entfernen — seit #1725 fragt hx-confirm vorher nach (wie Goals, C6).
            page.once("dialog", lambda dialog: dialog.accept())
            card.locator('[data-testid="field-row"]:has-text("Dauer") [data-testid="field-remove-btn"]').click()
            expect(card.locator('[data-testid="field-row"]')).to_have_count(0, timeout=10000)

            # Gruppe löschen.
            card.locator('[data-testid="group-delete-btn"]').click()
            expect(page.locator(f'[data-testid="field-group"][data-group-id="{gpk}"]')).to_have_count(0, timeout=10000)
        finally:
            _delete_doctype(page, base_url, pk)


# ---------------------------------------------------------------------------
# Drag-and-Drop-Sortierung
# ---------------------------------------------------------------------------


class TestFieldBoardDragAndDrop:
    """Gruppen-Reorder + Cross-Gruppen-Feld-Move, beide ueber die native Maus-Sequenz."""

    def test_group_reorder_persists(self, authenticated_page, base_url):
        """(a) Gruppen-Reihenfolge tauschen → nach Reload persistiert.

        Refs #1855: Hier stand bis zur 0.94-Welle ein ``xfail`` — der Test
        benutzte Playwrights synthetisches ``drag_to``, das bei SortableJS je
        Browser-Session gelegentlich gar kein ``onEnd`` mit Aenderung ausloest
        (alle drei Wiederholungen derselben Session waren dann betroffen).
        Root-Cause ist damit **die Emulation**, nicht das Board: die native
        Maus-Sequenz ``_native_drag`` — mit der die drei Cross-Container-Faelle
        derselben Klasse laengst arbeiten (Plan-Risiko 1, Refs #1056/P9) —
        loest ``onEnd`` deterministisch aus, weil SortableJS die
        Zwischen-``dragover``-Schritte sieht. Deshalb derselbe Weg auch hier,
        und der Marker faellt weg.
        """
        page = authenticated_page
        pk = _create_doctype(page, base_url, _cfg_name("DT-DND-G"))
        try:
            g1 = _add_group(page, _cfg_name("A"))
            g2 = _add_group(page, _cfg_name("B"))
            assert _group_order(page) == [g1, g2], "Ausgangsreihenfolge unerwartet"

            # Handle von g1 auf die UNTERE Haelfte der g2-Karte ziehen → g1
            # rutscht hinter g2 (Gegenstueck zu ``y_frac=0.12`` im
            # Top-Level-Move, wo VOR der Zielkarte eingefuegt werden soll).
            # ``_native_drag`` wartet selbst auf den ``/reorder/``-POST, bevor
            # es zurueckkehrt — der folgende ``goto()`` kann ihn also nicht
            # mehr abbrechen.
            handle = page.locator(f'[data-testid="field-group"][data-group-id="{g1}"] .group-drag-handle')
            target = page.locator(f'[data-testid="field-group"][data-group-id="{g2}"]')
            self._native_drag(page, handle, target, y_frac=0.85)

            # Persistenz gegen einen frischen Full-Load prüfen.
            page.goto(f"{base_url}{DOCTYPE_LIST}{pk}/", wait_until="domcontentloaded")
            expect(page.locator('[data-testid="field-group"]')).to_have_count(2, timeout=10000)
            assert _group_order(page) == [g2, g1], "Gruppen-Reihenfolge nicht persistiert"
        finally:
            _delete_doctype(page, base_url, pk)

    def test_move_field_across_groups_persists(self, authenticated_page, base_url):
        """(b) Feld per Maus-Sequenz in die andere Gruppe → nach Reload persistiert.

        Cross-Container-Move über die native Maus-Sequenz (mouse.down auf dem
        ``.field-drag-handle`` → mehrstufiges ``mouse.move`` auf eine Feldzeile der
        Zielgruppe → Pause → ``mouse.up``), da Playwrights ``drag_to`` den
        Container-Wechsel bei SortableJS nicht zuverlässig auslöst (Plan-Risiko 1,
        Refs #1056/P9). Ziel ist bewusst eine bestehende Feldzeile der Zielgruppe
        (tief in deren Feld-Liste) — ein eindeutiges Drop-Ziel, unabhängig davon,
        ob die Zielgruppe ober- oder unterhalb der Quelle liegt.
        """
        page = authenticated_page
        pk = _create_doctype(page, base_url, _cfg_name("DT-DND-M"))
        try:
            src = _cfg_name("SRC")
            dst = _cfg_name("DST")
            _add_group(page, src)
            dst_pk = _add_group(page, dst)
            # Zwei Felder in die Quellgruppe; die Zielgruppe bekommt eine Ankerzeile.
            _assign_field(page, "Dauer", src)
            moved = _assign_field(page, "Straßenkontakt", src)
            anchor = _assign_field(page, "Notiz", dst)

            handle = page.locator(f'[data-dtf-id="{moved}"] .field-drag-handle')
            target = page.locator(f'ul[data-sortable="fields"][data-group-id="{dst_pk}"] li[data-dtf-id="{anchor}"]')
            self._native_drag(page, handle, target)

            # Persistenz gegen einen frischen Full-Load prüfen: das verschobene Feld
            # sitzt jetzt in der Zielgruppe (neben der Ankerzeile).
            page.goto(f"{base_url}{DOCTYPE_LIST}{pk}/", wait_until="domcontentloaded")
            moved_in_dst = page.locator(f'ul[data-sortable="fields"][data-group-id="{dst_pk}"] [data-dtf-id="{moved}"]')
            expect(moved_in_dst).to_have_count(1, timeout=10000)
            # Und nicht mehr als freie Top-Level-Zeile.
            expect(page.locator(f'[data-testid="free-field-row"][data-dtf-id="{moved}"]')).to_have_count(0)
        finally:
            _delete_doctype(page, base_url, pk)

    def test_move_free_field_into_group_persists(self, authenticated_page, base_url):
        """(c) Freies Top-Level-Feld per Maus-Sequenz in eine Gruppe → persistiert (#1713)."""
        page = authenticated_page
        pk = _create_doctype(page, base_url, _cfg_name("DT-DND-FG"))
        try:
            dst = _cfg_name("DST")
            dst_pk = _add_group(page, dst)
            # Die Gruppe bekommt bereits ein Feld → auf dessen Zeile (tief in der
            # Feld-Liste) wird gedroppt, damit das nested fields-Sortable eindeutig
            # gewinnt (die umgebende Blocks-Liste würde bei einem Karten-Treffer
            # nur reordern).
            anchor_id = _assign_field(page, "Notiz", dst)
            # „Dauer" als freien Top-Level-Block zuordnen, dann in die Gruppe ziehen.
            free_id = _assign_free_field(page, "Dauer")
            handle = page.locator(f'[data-dtf-id="{free_id}"] .field-drag-handle')
            target = page.locator(f'ul[data-sortable="fields"][data-group-id="{dst_pk}"] li[data-dtf-id="{anchor_id}"]')
            self._native_drag(page, handle, target)

            page.goto(f"{base_url}{DOCTYPE_LIST}{pk}/", wait_until="domcontentloaded")
            in_group = page.locator(f'ul[data-sortable="fields"][data-group-id="{dst_pk}"] [data-dtf-id="{free_id}"]')
            expect(in_group).to_have_count(1, timeout=10000)
            # Keine freie Top-Level-Zeile mehr für dieses Feld.
            expect(page.locator(f'[data-testid="free-field-row"][data-dtf-id="{free_id}"]')).to_have_count(0)
        finally:
            _delete_doctype(page, base_url, pk)

    def test_move_grouped_field_to_top_level_between_groups(self, authenticated_page, base_url):
        """(b) Feld aus Gruppe an Top-Level-Position ZWISCHEN zwei Gruppen (#1713).

        Der frühere „Lücken-Drop landet in der Nachbargruppe"-Effekt ist damit ein
        Feature: das Feld wird ein eigenständiger freier Block zwischen A und B.
        """
        page = authenticated_page
        pk = _create_doctype(page, base_url, _cfg_name("DT-DND-TL"))
        try:
            a = _cfg_name("A")
            b = _cfg_name("B")
            ga = _add_group(page, a)
            gb = _add_group(page, b)
            moved = _assign_field(page, "Dauer", a)

            # Handle des Feldes in Gruppe A auf den OBEREN Rand der B-Karte ziehen
            # (y_frac klein → Insert VOR B im Blocks-Container, nicht in B hinein).
            handle = page.locator(f'[data-dtf-id="{moved}"] .field-drag-handle')
            target = page.locator(f'[data-testid="field-group"][data-group-id="{gb}"]')
            self._native_drag(page, handle, target, y_frac=0.12)

            page.goto(f"{base_url}{DOCTYPE_LIST}{pk}/", wait_until="domcontentloaded")
            expect(page.locator(f'[data-testid="free-field-row"][data-dtf-id="{moved}"]')).to_have_count(
                1, timeout=10000
            )
            assert _block_order(page) == [ga, f"dtf:{moved}", gb], (
                f"Feld nicht als freier Block zwischen den Gruppen: {_block_order(page)}"
            )
        finally:
            _delete_doctype(page, base_url, pk)

    @staticmethod
    def _center_for_drag(page, handle, target):
        """Zentriert die Quellzeile und liefert stabile Bounding-Boxes (Quelle, Ziel).

        Warum wiederholt zentriert wird: Nach jeder Board-Mutation lädt die
        Live-Vorschau-Pane (P11) asynchron neu. Deren ``htmx:afterSwap`` fokussiert
        über ``focus-management.js`` (Refs #1339) das getauschte Vorschau-Fragment
        am SEITENENDE mit ``preventScroll:false`` — der Viewport springt ans Ende
        (``document.activeElement`` == ``config-preview-pane``, ``scrollY`` == max).
        Seit der Regel-Editor (M1 / Refs #1057) die Detailseite verlängert hat,
        landet das Feld-Board bei diesem Scroll-Reflex OBERHALB des Viewports
        (negative ``y`` → die Maus-Sequenz läuft ins Leere). Der Reflex feuert
        genau EINMAL pro Mutation und kann ein einzelnes ``scrollIntoView``
        überholen. Daher zentrieren, bis Quelle UND Ziel stabil im Viewport
        liegen — das Re-Zentrieren nach dem einmaligen Fokus-Sprung hält.
        """
        vh = page.viewport_size["height"]
        hb = tb = None
        for _ in range(12):
            handle.evaluate("el => el.scrollIntoView({block: 'center'})")
            page.wait_for_timeout(150)
            hb = handle.bounding_box()
            tb = target.bounding_box()
            if hb and tb and 0 <= hb["y"] <= vh - hb["height"] and 0 <= tb["y"] <= vh - tb["height"]:
                break
        return hb, tb

    @classmethod
    def _native_drag(cls, page, handle, target, *, y_frac=0.5):
        """Zieht ``handle`` per nativer Maus-Sequenz auf ``target`` (SortableJS-tauglich).

        ``y_frac`` steuert die vertikale Auftreffhöhe im Ziel: 0.5 = Mitte
        (Standard, z. B. für leere Feld-Listen), ein kleiner Wert (~0.15) zielt
        auf den oberen Rand einer Ziel-Karte, um bei einem Block-Ziel ein Insert
        VOR der Karte zu erzwingen (statt dahinter/hinein).
        """
        # Zentrieren statt minimal scrollen: das Ziel kann OBERHALB der Quellzeile
        # liegen — mit der Quellzeile in der Viewport-Mitte sind beide sichtbar.
        # Robust gegen den asynchronen Fokus-Scroll der Live-Vorschau (Details in
        # ``_center_for_drag``): wiederholt zentrieren, bis beide Boxen stabil im
        # Viewport liegen, DANN Koordinaten nehmen.
        hb, tb = cls._center_for_drag(page, handle, target)
        assert hb and tb, "Bounding-Box für Drag-Quelle/-Ziel nicht verfügbar"
        assert hb["y"] >= 0 and tb["y"] >= 0, (
            f"Drag-Elemente außerhalb des Viewports (handle y={hb['y']}, ziel y={tb['y']})"
        )
        page.mouse.move(hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2)
        # Komplette Geste (down/move/up) läuft INNERHALB des Waiters auf die
        # Antwort des Sortable-onEnd-POST statt fest zu schlafen (Refs
        # #1855/#2235): unter Host-Last dauert der Board-POST länger als jede
        # Pause, und der anschließende ``goto()`` bricht einen noch laufenden
        # POST ab — die Reihenfolge wäre dann nie persistiert. Der Waiter
        # muss VOR ``mouse.up()`` scharf sein, weil SortableJS ``onEnd``
        # genau daran hängt; da er die gesamte Geste umschließt, kann keine
        # Zwischenstufe ihn verpassen. Alle drei Board-Endpunkte
        # (``reorder/blocks``, ``reorder/fields``, ``reorder/move-field``)
        # liegen unter ``/reorder/`` — welcher feuert, entscheidet
        # ``config-sortable.js`` je nach Quell-/Ziel-Container.
        with page.expect_response(
            lambda r: "/reorder/" in r.url and r.request.method == "POST" and r.ok,
            timeout=15000,
        ):
            page.mouse.down()
            # Mehrstufige Bewegung, damit SortableJS die dragover-Events sieht.
            # Ziel-Offset mit Untergrenze: eine leere Ziel-Liste ist nur
            # min-h-[1.5rem] hoch — der Auftreffpunkt darf nicht auf der Kante liegen.
            page.mouse.move(tb["x"] + tb["width"] / 2, tb["y"] + max(tb["height"] * y_frac, 10), steps=25)
            page.wait_for_timeout(300)
            page.mouse.up()

    @classmethod
    def _native_move(cls, page, dtf_id, target_group_pk):
        """Feld-Zeile per nativer Maus-Sequenz in die Ziel-Gruppenliste ziehen.

        Der Handle-Selektor ``[data-dtf-id] .field-drag-handle`` matcht sowohl
        eine gruppierte ``li``-Feldzeile als auch eine freie Top-Level-Zeile
        (``div[data-testid="free-field-row"]``).
        """
        handle = page.locator(f'[data-dtf-id="{dtf_id}"] .field-drag-handle')
        target = page.locator(f'ul[data-sortable="fields"][data-group-id="{target_group_pk}"]')
        cls._native_drag(page, handle, target)


# ---------------------------------------------------------------------------
# Interleaving im Event-Formular (globale Block-Sequenz, #1713)
# ---------------------------------------------------------------------------


class TestEventFormInterleaving:
    """Ein Typ mit freiem Feld → Gruppe → freiem Feld rendert im Event-Formular
    interleaved (Gruppe ZWISCHEN den beiden freien Feldern)."""

    def test_event_form_renders_interleaved_blocks(self, authenticated_page, base_url):
        page = authenticated_page
        name = _cfg_name("DT-IL")
        pk = _create_doctype(page, base_url, name)
        try:
            # Blockpos 0: freies Feld „Dauer"; Blockpos 1: Gruppe mit „Notiz";
            # Blockpos 2: freies Feld „Straßenkontakt".
            _assign_free_field(page, "Dauer")
            mid = _cfg_name("MID")
            _add_group(page, mid)
            _assign_field(page, "Notiz", mid)
            _assign_free_field(page, "Straßenkontakt")

            # Event-Formular öffnen und den Wegwerf-Typ wählen. Auf die MID-Gruppe
            # warten (nur dieser Typ hat sie) — der HTMX-Swap ist damit fertig und
            # wir messen nicht versehentlich den vorgerenderten Seed-Typ.
            page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
            page.select_option("select[name='document_type']", label=name)
            page.locator(f'#dynamic-fields h2:has-text("{mid}")').wait_for(state="visible", timeout=10000)

            # Vertikale Reihenfolge: Dauer (frei) < Gruppen-Header < Straßenkontakt (frei).
            dauer_y = page.locator('#dynamic-fields [name="dauer"]').bounding_box()["y"]
            header_y = page.locator(f'#dynamic-fields h2:has-text("{mid}")').bounding_box()["y"]
            strasse_y = page.locator('#dynamic-fields [name="strassenkontakt"]').bounding_box()["y"]
            assert dauer_y < header_y < strasse_y, (
                f"Event-Formular nicht interleaved: dauer={dauer_y}, header={header_y}, strasse={strasse_y}"
            )
        finally:
            _delete_doctype(page, base_url, pk)


# ---------------------------------------------------------------------------
# Registry-Auskunft „Erscheint in …" + Slug-Sichtbarkeit (E2, Refs #1848)
# ---------------------------------------------------------------------------


class TestReportBindingsAndSlugVisibility:
    """Konfigurations-UI zeigt jetzt die Registry-Auskunft + den Slug an.

    Detailseite ist reines Server-Render (kein HTMX-Swap für die neuen
    Blöcke; nur die Live-Vorschau-Pane bleibt HTMX und ist hier unberührt) —
    ``to_be_visible``/``to_contain_text`` genügen ohne Swap-bewusste Waits.
    """

    def test_doctype_detail_shows_report_bindings_only_for_event(self, authenticated_page, base_url):
        """Wegwerf-Typ crisis/event → Badge sichtbar; entity_type=client → Abschnitt fehlt."""
        page = authenticated_page
        pk = _create_doctype(page, base_url, _cfg_name("DT-BIND"), system_type_value="crisis")
        client_pk = None
        try:
            page.goto(f"{base_url}{DOCTYPE_LIST}{pk}/", wait_until="domcontentloaded")
            bindings = page.locator('[data-testid="doctype-report-bindings"]')
            expect(bindings).to_be_visible(timeout=10000)
            expect(bindings).not_to_contain_text("Erscheint in keinem Bericht.")

            # entity_type=client: der Abschnitt fehlt komplett — an Personen-Typen
            # ist die Berichtsfrage gegenstandslos (Konzept #1847 §E2).
            client_name = _cfg_name("DT-BIND-CLIENT")
            page.goto(f"{base_url}{DOCTYPE_LIST}new/", wait_until="domcontentloaded")
            page.fill("input#id_name", client_name)
            page.select_option("select#id_entity_type", value="client")
            page.locator(FORM_SUBMIT).click()
            page.wait_for_url(_DETAIL_RE)
            match = _DETAIL_RE.search(page.url)
            assert match, f"Detail-URL nicht erkannt: {page.url}"
            client_pk = match.group(1)
            expect(page.locator('[data-testid="doctype-report-bindings"]')).to_have_count(0)
        finally:
            _delete_doctype(page, base_url, pk)
            if client_pk:
                _delete_doctype(page, base_url, client_pk)

    def test_doctype_without_binding_shows_explicit_empty_state(self, authenticated_page, base_url):
        """Ohne Berichtsschlüssel erscheint der Typ in keinem Bericht — ausdrücklich gesagt."""
        page = authenticated_page
        pk = _create_doctype(page, base_url, _cfg_name("DT-NOBIND"))
        try:
            page.goto(f"{base_url}{DOCTYPE_LIST}{pk}/", wait_until="domcontentloaded")
            bindings = page.locator('[data-testid="doctype-report-bindings"]')
            expect(bindings).to_be_visible(timeout=10000)
            expect(bindings).to_contain_text("Erscheint in keinem Bericht.")
        finally:
            _delete_doctype(page, base_url, pk)

    def test_fieldtemplate_edit_shows_report_binding(self, authenticated_page, base_url):
        """Seed-Feldvorlage „Dauer" (Slug ``dauer``) speist Beispiel: Beratungsstunden (E1).

        GET-only — der Slug ist facility-weit eindeutig (kein Wegwerf-Duplikat
        möglich), deshalb an der Seed-Vorlage selbst geprüft statt an einer neu
        angelegten. Mutiert den Seed-Bestand nicht (Modul-Konvention).
        """
        page = authenticated_page
        page.goto(f"{base_url}{FIELDTEMPLATE_LIST}?q=Dauer", wait_until="domcontentloaded")
        row = page.locator('[data-testid="fieldtemplate-row"]:has-text("Dauer")').first
        expect(row).to_be_visible(timeout=10000)
        row.locator('[data-testid="fieldtemplate-edit-link"]').click()
        page.wait_for_url(re.compile(r"/config/field-templates/[0-9a-f-]+/edit/$"))
        bindings = page.locator('[data-testid="fieldtemplate-report-bindings"]')
        expect(bindings).to_be_visible(timeout=10000)
        expect(bindings).to_contain_text("Beratungsstunden")

    def test_report_key_select_is_editable_until_it_carries_a_value(self, authenticated_page, base_url):
        """E3 (Refs #1849): das ``report_key``-Select traegt die Registry-Optionen,
        und NULL→gesetzt geht ueber die UI (Entscheid E-K).

        Angelegt wird bewusst OHNE Berichtsschluessel (der Wegwerf-Typ waehlt
        keinen Systemtyp, die Browser-Vorbelegung greift also nicht) — erst die
        Bearbeitung setzt ihn. Danach ist das Feld gesperrt: derselbe Durchlauf
        zeigt beide Haelften der Regel.
        """
        page = authenticated_page
        pk = _create_doctype(page, base_url, _cfg_name("DT-RKEY"))
        try:
            page.goto(f"{base_url}{DOCTYPE_LIST}{pk}/edit/", wait_until="domcontentloaded")
            auswahl = page.locator("select#id_report_key")
            expect(auswahl).to_be_visible(timeout=10000)
            expect(auswahl).to_be_enabled()
            # Sentinel + mindestens ein Registry-Schluessel stehen zur Wahl.
            expect(auswahl.locator("option[value='excluded']")).to_have_count(1)
            expect(auswahl.locator("option[value='counseling']")).to_have_count(1)

            page.select_option("select#id_report_key", value="counseling")
            page.locator(FORM_SUBMIT).click()
            page.wait_for_url(re.compile(rf"/config/document-types/{pk}/$"))

            # Gesetzt → gesperrt; die Registry-Auskunft nennt jetzt den Bericht.
            bindings = page.locator('[data-testid="doctype-report-bindings"]')
            expect(bindings).to_contain_text("Beispiel-Sachbericht", timeout=10000)
            page.goto(f"{base_url}{DOCTYPE_LIST}{pk}/edit/", wait_until="domcontentloaded")
            expect(page.locator("select#id_report_key")).to_be_disabled()
        finally:
            _delete_doctype(page, base_url, pk)

    def test_doctype_detail_and_list_show_slug(self, authenticated_page, base_url):
        """Slug (E-F) sichtbar in Detail- UND Listenansicht, nicht editierbar."""
        page = authenticated_page
        name = _cfg_name("DT-SLUG")
        pk = _create_doctype(page, base_url, name)
        try:
            expected_slug = name.lower()  # keine Umlaute → slugify == lower()

            page.goto(f"{base_url}{DOCTYPE_LIST}{pk}/", wait_until="domcontentloaded")
            expect(page.locator('[data-testid="doctype-slug"]')).to_contain_text(expected_slug, timeout=10000)

            page.goto(f"{base_url}{DOCTYPE_LIST}?q={name}", wait_until="domcontentloaded")
            row = page.locator(f'[data-testid="doctype-row"]:has-text("{name}")')
            expect(row).to_be_visible(timeout=10000)
            expect(row.locator('[data-testid="doctype-slug"]')).to_contain_text(expected_slug)
        finally:
            _delete_doctype(page, base_url, pk)


# ---------------------------------------------------------------------------
# Regression: obfuskierter Django-Admin bleibt erreichbar
# ---------------------------------------------------------------------------


class TestAdminMgmtRegression:
    """Die neue Config-UI verdrängt den bestehenden Admin nicht."""

    def test_admin_mgmt_reachable_as_admin(self, authenticated_page, base_url):
        """``/admin-mgmt/`` liefert als admin kein 5xx (Redirect im Sudo-Flow ist OK)."""
        resp = authenticated_page.goto(f"{base_url}/admin-mgmt/", wait_until="domcontentloaded")
        assert resp.status < 500, f"admin-mgmt lieferte {resp.status}"
