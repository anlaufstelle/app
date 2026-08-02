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

from ._helpers import track_console_errors

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


# ---------------------------------------------------------------------------
# UI-Helfer (alle mandantengescoped über die eingeloggte Session)
# ---------------------------------------------------------------------------


def _create_doctype(page, base_url, name, system_type_value="note") -> str:
    """Legt einen Wegwerf-Dokumentationstyp an; gibt dessen UUID (aus der URL) zurück.

    ``system_type`` wird bewusst gesetzt (Plan: „system_type wählen!") — nur beim
    Anlegen editierbar, bei der Bearbeitung serverseitig unveränderbar/disabled.
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


def _group_order(page) -> list[str]:
    """DOM-Reihenfolge der Gruppen-pks im Sortable-Container (persistierte Ansicht)."""
    cards = page.locator('[data-sortable="groups"] > [data-group-id]')
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
        pk = _create_doctype(page, base_url, name, system_type_value="note")
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

            # Feld entfernen.
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
    """Gruppen-Reorder (drag_to) + Cross-Gruppen-Feld-Move (native Maus-Sequenz)."""

    def test_group_reorder_persists(self, authenticated_page, base_url):
        """(a) Gruppen-Reihenfolge per ``drag_to`` tauschen → nach Reload persistiert."""
        page = authenticated_page
        pk = _create_doctype(page, base_url, _cfg_name("DT-DND-G"))
        try:
            g1 = _add_group(page, _cfg_name("A"))
            g2 = _add_group(page, _cfg_name("B"))
            assert _group_order(page) == [g1, g2], "Ausgangsreihenfolge unerwartet"

            # Gruppen-Reorder im selben Container funktioniert mit drag_to
            # (manuell verifiziert, Refs #1056/P9). Handle von g1 auf die
            # g2-Karte ziehen → g1 rutscht hinter g2.
            handle = page.locator(f'[data-testid="field-group"][data-group-id="{g1}"] .group-drag-handle')
            target = page.locator(f'[data-testid="field-group"][data-group-id="{g2}"]')
            handle.drag_to(target)
            # Board-Swap nach dem Sortable-onEnd-POST abwarten.
            page.wait_for_timeout(1200)

            # Persistenz gegen einen frischen Full-Load prüfen.
            page.goto(f"{base_url}{DOCTYPE_LIST}{pk}/", wait_until="domcontentloaded")
            expect(page.locator('[data-testid="field-group"]')).to_have_count(2, timeout=10000)
            assert _group_order(page) == [g2, g1], "Gruppen-Reihenfolge nicht persistiert"
        finally:
            _delete_doctype(page, base_url, pk)

    def test_move_field_across_groups_persists(self, authenticated_page, base_url):
        """(b) Feld per Maus-Sequenz in die andere Gruppe → nach Reload persistiert.

        Cross-Container-Move über die native Maus-Sequenz (mouse.down auf dem
        ``.field-drag-handle`` → mehrstufiges ``mouse.move`` auf die leere
        Ziel-``ul`` → Pause → ``mouse.up``), da Playwrights ``drag_to`` den
        Container-Wechsel bei SortableJS nicht zuverlässig auslöst (Plan-Risiko 1,
        Refs #1056/P9). Der native Weg lief in der Verifikation stabil; die
        Fallback-Variante über den ``move-field``-Endpunkt (``page.request.post``)
        wurde nicht benötigt.
        """
        page = authenticated_page
        pk = _create_doctype(page, base_url, _cfg_name("DT-DND-M"))
        try:
            src = _cfg_name("SRC")
            dst = _cfg_name("DST")
            _add_group(page, src)
            dst_pk = _add_group(page, dst)
            # Zwei Felder in die Quellgruppe, Zielgruppe bleibt leer.
            _assign_field(page, "Dauer", src)
            moved = _assign_field(page, "Straßenkontakt", src)

            self._native_move(page, moved, dst_pk)

            # Persistenz gegen einen frischen Full-Load prüfen.
            page.goto(f"{base_url}{DOCTYPE_LIST}{pk}/", wait_until="domcontentloaded")
            dst_rows = page.locator(f'ul[data-sortable="fields"][data-group-id="{dst_pk}"] [data-dtf-id]')
            expect(dst_rows).to_have_count(1, timeout=10000)
            assert dst_rows.first.get_attribute("data-dtf-id") == moved, "Feld nicht in Zielgruppe persistiert"
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
    def _native_move(cls, page, dtf_id, target_group_pk):
        """Feld-Zeile per nativer Maus-Sequenz in die Ziel-Gruppenliste ziehen."""
        handle = page.locator(f'li[data-dtf-id="{dtf_id}"] .field-drag-handle')
        target = page.locator(f'ul[data-sortable="fields"][data-group-id="{target_group_pk}"]')
        # Zentrieren statt minimal scrollen: die Ziel-Liste kann OBERHALB der
        # Quellzeile liegen (Gruppen mit gleichem sort_order ordnen nach Name) —
        # mit der Quellzeile in der Viewport-Mitte sind beide Karten sichtbar.
        # Robust gegen den asynchronen Fokus-Scroll der Live-Vorschau (Details in
        # ``_center_for_drag``): wiederholt zentrieren, bis beide Boxen stabil im
        # Viewport liegen, DANN Koordinaten nehmen.
        hb, tb = cls._center_for_drag(page, handle, target)
        assert hb and tb, "Bounding-Box für Drag-Quelle/-Ziel nicht verfügbar"
        assert hb["y"] >= 0 and tb["y"] >= 0, (
            f"Drag-Elemente außerhalb des Viewports (handle y={hb['y']}, ziel y={tb['y']})"
        )
        page.mouse.move(hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2)
        page.mouse.down()
        # Mehrstufige Bewegung, damit SortableJS die dragover-Events sieht.
        # Ziel-Offset mit Untergrenze: eine leere Ziel-Liste ist nur
        # min-h-[1.5rem] hoch — der Mittelpunkt darf nicht auf der Kante liegen.
        page.mouse.move(tb["x"] + tb["width"] / 2, tb["y"] + max(tb["height"] / 2, 10), steps=25)
        page.wait_for_timeout(300)
        page.mouse.up()
        # Board-Swap nach dem move-field-POST abwarten.
        page.wait_for_timeout(900)


# ---------------------------------------------------------------------------
# Regression: obfuskierter Django-Admin bleibt erreichbar
# ---------------------------------------------------------------------------


class TestAdminMgmtRegression:
    """Die neue Config-UI verdrängt den bestehenden Admin nicht."""

    def test_admin_mgmt_reachable_as_admin(self, authenticated_page, base_url):
        """``/admin-mgmt/`` liefert als admin kein 5xx (Redirect im Sudo-Flow ist OK)."""
        resp = authenticated_page.goto(f"{base_url}/admin-mgmt/", wait_until="domcontentloaded")
        assert resp.status < 500, f"admin-mgmt lieferte {resp.status}"
