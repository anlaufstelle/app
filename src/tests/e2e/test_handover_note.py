"""E2E-Tests der manuellen Schichtnotiz in der Übergabe (Refs #2042).

Prüft die vier Zusagen, die nur im Browser belegbar sind:

- Die Notiz steht **prominent** — vor den Kennzahlen — in der Übergabe der
  Folgeschicht.
- Innerhalb des Editierfensters lässt sie sich anlegen und ändern.
- Nach Ablauf des Fensters ist sie schreibgeschützt (kein Formular).
- Die Assistenz sieht sie gar nicht (kein Platzhalter, kein Hinweis).

Dazu der a11y-Nachweis am Bedienpfad: Label-Bindung, Tastaturweg vom Textfeld
zum Speichern-Knopf und der Fokus nach dem HTMX-Swap.

Seeds hängen an ``name="Hauptstelle"`` bzw. am Admin-Benutzer, nie an der
physischen Zeilenreihenfolge (Refs #2166 — die UUID-PK würfelt sie).
"""

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell

pytestmark = pytest.mark.e2e

VERGANGENES_DATUM = "2026-04-15"


def _shell(e2e_env, script):
    return django_shell(e2e_env, script)


def _speichern(page, block_testid, text):
    """Text eintragen, speichern und auf den **tatsaechlich vollzogenen** Swap warten.

    **Warte-Falle** (Refs #2042, Fix-Runde 1): ``expect_response`` kehrt zurueck,
    sobald die ANTWORT da ist — htmx hat den Container zu diesem Zeitpunkt noch
    nicht ersetzt. Wer direkt danach das naechste Mal ``fill``t, schreibt in ein
    Textfeld, das der Swap gleich darauf verwirft; der zweite Klick schickte
    dann den ALTEN Text zurueck.

    **Nachbesserung Fix-Runde 2** Befund E-14): Auf „Statusmeldung
    vorhanden" und „Feldwert stimmt" zu warten genuegt nicht — beim ZWEITEN
    Speichern steht die Statusmeldung schon vom ersten im DOM, und ``fill``
    hat den erwarteten Feldwert bereits vor dem Request gesetzt. Beide
    Bedingungen koennen also am ALTEN Container wahr sein. Beobachtet wird
    deshalb der Knoten selbst: ``hx-swap="outerHTML"`` auf ``#handover-note``
    ersetzt ihn, der alte Knoten verliert seine Verbindung zum Dokument
    (``isConnected === false``). Das ist der einzige Zustand, den der alte
    Container nicht schon vorher hatte.
    """
    alter_container = page.locator("#handover-note").element_handle()
    block = page.locator(f"[data-testid='{block_testid}']")
    expect(block).to_be_visible()
    block.locator("textarea").fill(text)
    with page.expect_response(lambda r: "/uebergabe/notiz/" in r.url and r.request.method == "POST"):
        block.locator("button[type='submit']").click()
    page.wait_for_function("node => !node.isConnected", arg=alter_container, timeout=15000)
    expect(page.locator("[data-testid='handover-note-status']")).to_contain_text("Übergabenotiz gespeichert.")
    expect(page.locator(f"[data-testid='{block_testid}'] textarea")).to_have_value(text)


class TestHandoverHtmxErrorSwap:
    def test_real_400_response_replaces_the_handover_partial(self, authenticated_page, base_url, eine_laufende_schicht):
        """Ein echter 400er zeigt seinen Partial-Fehler im bestehenden HTMX-Ziel."""
        page = authenticated_page
        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        form = page.locator("[data-testid='handover-note-current'] form")
        expect(form).to_be_visible()
        # Der Zeitstrom-Watch pollt beim Start sofort und ersetzt den Notizblock,
        # solange kein Formular offen ist (Fokus oder geaenderter Entwurf). Ohne
        # Fokus wuerde er das praeparierte Formular zwischen Feldmanipulation und
        # Klick austauschen und der POST liefe mit gueltigen Koordinaten (200).
        form.locator("textarea").focus()
        old_container = page.locator("#handover-note").element_handle()
        form.locator("input[name='time_filter']").evaluate("element => element.value = 'not-a-uuid'")

        with page.expect_response(
            lambda response: (
                "/uebergabe/notiz/" in response.url and response.request.method == "POST" and response.status == 400
            )
        ):
            form.locator("button[type='submit']").click()

        page.wait_for_function("node => !node.isConnected", arg=old_container, timeout=15000)
        expect(page.locator("#handover-note [role='alert']")).to_contain_text(
            "Die Schicht konnte nicht bestimmt werden"
        )
        expect(page.locator("[data-testid='htmx-error-toast']")).to_have_count(0)


def _als_zweite_person_speichern(e2e_env, text):
    _shell(
        e2e_env,
        "from core.models import Facility, User;"
        " from core.services.case.handover_note import current_shift, save_note;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.filter(facility=f, role=User.Role.LEAD, is_active=True).first();"
        f" save_note(f, current_shift(f), u, {text!r});"
        " print('ok')",
    )


@pytest.fixture
def leere_notizen(e2e_env):
    """Definierter Ausgangsstand: keine Notizen, Dienstplan wie geseedet.

    Die Schichtwechsel-Tests unten veraendern die Zeitfenster der Einrichtung.
    Ohne Ruecksetzen haenge das Ergebnis der uebrigen Faelle an der
    Ausfuehrungsreihenfolge — deshalb hier: Notizen loeschen (erst dadurch
    werden die Testfilter ueberhaupt loeschbar, ``on_delete=PROTECT``), die
    eigenen ``E2E-*``-Filter entfernen und die geseedeten wieder aktivieren.
    """
    _shell(
        e2e_env,
        "from core.models import Facility, ShiftHandoverNote, TimeFilter;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " ShiftHandoverNote.objects.filter(facility=f).delete();"
        " TimeFilter.objects.filter(facility=f, label__startswith='E2E-').delete();"
        " TimeFilter.objects.filter(facility=f).update(is_active=True);"
        " print('ok')",
    )
    return True


@pytest.fixture
def eine_laufende_schicht(e2e_env, leere_notizen):
    """Genau EIN Zeitfenster, das die aktuelle Uhrzeit sicher deckt.

    Die Seed-Schichten (Frueh/Spaet/Nacht) machen den Test von der Uhrzeit des
    Laufs abhaengig. Ein Fenster 00:00–23:59 waere ebenfalls nicht sekundengenau
    richtig — und am Tagesrand riss die frueher hier gerechnete Grenze
    „jetzt minus eine Minute" den Ablauf ganz auf (Refs #2042, Befund
    E-16): um 00:00:30 wurde daraus 23:59, und der Wechsel fand nicht statt.

    Deshalb ein Fenster **relativ zu jetzt, mit Mitternachts-Ueberlauf**:
    ``start = jetzt - 5 min``, ``end = start - 1 min``. Weil ``start > end``
    ist, laeuft das Fenster von ``start`` bis zur selben Uhrzeit am Folgetag und
    deckt den aktuellen Zeitpunkt garantiert — auch um 00:00:30 (dann greift
    das Fenster des VORtags, genau wie bei jeder Nachtschicht).

    Die Bestandsfilter werden deaktiviert statt geloescht — an ihnen koennen
    Notizen frueherer Testfaelle haengen (``on_delete=PROTECT``).
    """
    _shell(
        e2e_env,
        "from core.models import Facility, TimeFilter;"
        " from django.utils import timezone;"
        " import datetime;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " TimeFilter.objects.filter(facility=f).update(is_active=False);"
        " TimeFilter.objects.filter(facility=f, label__startswith='E2E-Schicht').delete();"
        " jetzt = timezone.localtime();"
        " start = (jetzt - datetime.timedelta(minutes=5)).time().replace(second=0, microsecond=0);"
        " ende = (jetzt - datetime.timedelta(minutes=6)).time().replace(second=0, microsecond=0);"
        " TimeFilter.objects.create(facility=f, label='E2E-Schicht A',"
        " start_time=start, end_time=ende, sort_order=0);"
        " print('ok')",
    )
    return True


@pytest.fixture
def ohne_zeitfilter(e2e_env, leere_notizen):
    """Kalenderfall: die Einrichtung hat keine aktiven Zeitfenster."""
    _shell(
        e2e_env,
        "from core.models import Facility, TimeFilter;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " TimeFilter.objects.filter(facility=f).update(is_active=False);"
        " print('ok')",
    )
    return True


@pytest.fixture
def alte_notiz(e2e_env, leere_notizen):
    """Notiz einer längst abgeschlossenen Schicht (Frühdienst, 2026-04-15).

    Liefert die ``time_filter``-UUID zurück, damit der Test genau diese
    Schicht ansteuern kann.
    """
    zeilen = _shell(
        e2e_env,
        "from core.models import Facility, TimeFilter, ShiftHandoverNote;"
        " from core.services.file_vault import encrypt_field;"
        " from core.services.case.handover_note import Shift, compute_shift_window;"
        " import datetime;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " tf = TimeFilter.objects.filter(facility=f, label='Frühdienst').first();"
        " s = Shift(datetime.date(2026, 4, 15), tf);"
        " w = compute_shift_window(f, s);"
        " n = ShiftHandoverNote.objects.create(facility=f, shift_date=s.shift_date,"
        " time_filter=tf, note=encrypt_field('E2E-Altnotiz aus dem Fruehdienst'),"
        " window_start=w.window_start, window_end=w.window_end, editable_until=w.editable_until);"
        " print('tf', tf.pk)",
    )
    return next(zeile.split(" ", 1)[1].strip() for zeile in zeilen if zeile.startswith("tf "))


class TestSchichtnotizSichtbarkeit:
    def test_notiz_der_letzten_schicht_steht_vor_den_kennzahlen(self, staff_page, base_url, alte_notiz):
        """Prominenz: der Notiz-Block liegt im DOM vor der Statistiken-Überschrift."""
        page = staff_page
        page.goto(
            f"{base_url}/?view=uebergabe&date={VERGANGENES_DATUM}&time_filter={alte_notiz}",
            wait_until="domcontentloaded",
        )
        block = page.locator("[data-testid='handover-note-previous']")
        expect(block).to_be_visible()
        expect(block).to_contain_text("E2E-Altnotiz aus dem Fruehdienst")

        reihenfolge = page.evaluate(
            "() => {"
            " const notiz = document.querySelector('#handover-note');"
            " const stats = [...document.querySelectorAll('h2')]"
            ".find(h => h.textContent.trim() === 'Statistiken');"
            " const folgt = notiz.compareDocumentPosition(stats) & Node.DOCUMENT_POSITION_FOLLOWING;"
            " return folgt ? 'notiz-zuerst' : 'stats-zuerst';"
            "}"
        )
        assert reihenfolge == "notiz-zuerst"

    def test_abgeschlossene_schicht_ist_schreibgeschuetzt(self, staff_page, base_url, alte_notiz):
        page = staff_page
        page.goto(
            f"{base_url}/?view=uebergabe&date={VERGANGENES_DATUM}&time_filter={alte_notiz}",
            wait_until="domcontentloaded",
        )
        block = page.locator("[data-testid='handover-note-previous']")
        expect(block).to_be_visible()
        assert block.locator("textarea").count() == 0
        expect(block).to_contain_text("Bearbeitung war bis")

    def test_assistenz_sieht_die_notiz_nicht(self, assistant_page, base_url, alte_notiz):
        page = assistant_page
        page.goto(
            f"{base_url}/?view=uebergabe&date={VERGANGENES_DATUM}&time_filter={alte_notiz}",
            wait_until="domcontentloaded",
        )
        page.locator("h2:has-text('Statistiken')").wait_for(state="visible", timeout=30000)
        assert page.locator("#handover-note").count() == 0
        assert page.get_by_text("E2E-Altnotiz aus dem Fruehdienst").count() == 0


class TestSchichtnotizBearbeiten:
    def test_fachkraft_schreibt_und_aendert_die_notiz_der_laufenden_schicht(self, staff_page, base_url, leere_notizen):
        page = staff_page
        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        _speichern(page, "handover-note-current", "E2E-Notiz Runde 1")
        _speichern(page, "handover-note-current", "E2E-Notiz Runde 2")

        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        expect(page.locator("[data-testid='handover-note-current'] textarea")).to_have_value("E2E-Notiz Runde 2")

    def test_leere_notiz_entfernt_den_eintrag(self, staff_page, base_url, leere_notizen, e2e_env):
        page = staff_page
        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        _speichern(page, "handover-note-current", "E2E-Notiz zum Loeschen")
        _speichern(page, "handover-note-current", "")

        zeilen = _shell(
            e2e_env,
            "from core.models import Facility, ShiftHandoverNote;"
            " f = Facility.objects.get(name='Hauptstelle');"
            " print('anzahl', ShiftHandoverNote.objects.filter(facility=f).count())",
        )
        assert any(zeile.strip() == "anzahl 0" for zeile in zeilen)

    def test_audit_zeile_ohne_notiztext(self, staff_page, base_url, leere_notizen, e2e_env):
        page = staff_page
        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        _speichern(page, "handover-note-current", "E2E-Auditprobe-Geheimtext")

        zeilen = _shell(
            e2e_env,
            "from core.models import AuditLog;"
            " e = AuditLog.objects.filter(action=AuditLog.Action.HANDOVER_NOTE_CREATED).order_by('-timestamp').first();"
            " print('target', e.target_type);"
            " print('detail', sorted(e.detail));"
            " print('leak', 'E2E-Auditprobe-Geheimtext' in str(e.detail))",
        )
        assert any(zeile.strip() == "target ShiftHandoverNote" for zeile in zeilen)
        assert any(zeile.strip() == "leak False" for zeile in zeilen)


class TestSchichtnotizLiveRefresh:
    def test_notiz_einer_zweiten_person_erscheint_ohne_reload(self, staff_page, base_url, leere_notizen, e2e_env):
        page = staff_page
        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        page.evaluate("() => window.zeitstromWatch.checkNow()")

        _als_zweite_person_speichern(e2e_env, "E2E-Live-Notiz von Nutzer A")
        page.evaluate("() => window.zeitstromWatch.checkNow()")

        expect(page.locator("[data-testid='handover-note-current'] textarea")).to_have_value(
            "E2E-Live-Notiz von Nutzer A"
        )

    def test_offener_entwurf_wird_vom_live_refresh_nie_ueberschrieben(
        self, staff_page, base_url, leere_notizen, e2e_env
    ):
        page = staff_page
        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        page.evaluate("() => window.zeitstromWatch.checkNow()")
        textfeld = page.locator("[data-testid='handover-note-current'] textarea")
        textfeld.fill("E2E-Ungespeicherter Entwurf")
        alter_container = page.locator("#handover-note").element_handle()

        _als_zweite_person_speichern(e2e_env, "E2E-Fremde neue Fassung")
        page.evaluate("() => window.zeitstromWatch.checkNow()")

        expect(textfeld).to_have_value("E2E-Ungespeicherter Entwurf")
        assert alter_container.evaluate("node => node.isConnected") is True


class TestSchreibenBisZurFolgeschicht:
    """E-11: der zusammenhaengende Ablauf, den die Einzelfaelle nicht abdecken.

    Schreiben und Anzeigen in der Folgeschicht wurden bis Fix-Runde 1 getrennt
    geprueft — genau in dieser Luecke sassen die Befunde E-03 und E-04.
    """

    def test_notiz_wandert_beim_schichtwechsel_in_den_abgebenden_block(
        self, staff_page, base_url, eine_laufende_schicht, e2e_env
    ):
        """E-11: der **tatsaechliche** Wechsel in die Folgeschicht.

        Fix-Runde 1 hat das nicht belegt: dort wurden nach dem Speichern
        dieselben Koordinaten wieder angesteuert — weder Serverzeit noch
        laufende Schicht wechselten, und die Notiz stand weiterhin im
        ``current``-Block.

        Hier wechselt die Schicht wirklich. Die Uhr laesst sich im E2E nicht
        stellen, also wird die Dienstplan-Konfiguration bewegt: die Schicht, in
        der geschrieben wurde, bekommt ein Ende in der Vergangenheit, und eine
        zweite Schicht deckt ab sofort die aktuelle Zeit ab. Damit ist die
        geschriebene Schicht **abgeschlossen**, die automatische Uebergabe
        waehlt sie als abgebende — und weil die Fenstergrenzen an der Zeile
        eingefroren sind (E-01), behaelt die Notiz ihre Identitaet.
        """
        page = staff_page
        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        _speichern(page, "handover-note-current", "E2E-Notiz der abgebenden Schicht")
        expect(page.locator("[data-testid='handover-note-current']")).to_contain_text(
            "E2E-Notiz der abgebenden Schicht"
        )

        # Schichtwechsel: A endet eine Minute VOR jetzt, B beginnt dort und
        # laeuft (mit Mitternachts-Ueberlauf) bis zur selben Uhrzeit am
        # Folgetag. Beide Grenzen liegen relativ zu ``jetzt``, keine feste
        # Uhrzeit — der Ablauf gilt damit auch um 00:00:30 (Refs #2042, E-16).
        _shell(
            e2e_env,
            "from core.models import Facility, TimeFilter;"
            " from django.utils import timezone;"
            " import datetime;"
            " f = Facility.objects.get(name='Hauptstelle');"
            " jetzt = timezone.localtime();"
            " grenze = (jetzt - datetime.timedelta(minutes=1)).time().replace(second=0, microsecond=0);"
            " vorher = (jetzt - datetime.timedelta(minutes=2)).time().replace(second=0, microsecond=0);"
            " tf = TimeFilter.objects.get(facility=f, label='E2E-Schicht A');"
            " tf.end_time = grenze; tf.save(update_fields=['end_time']);"
            " TimeFilter.objects.create(facility=f, label='E2E-Schicht B',"
            " start_time=grenze, end_time=vorher, sort_order=1);"
            " print('ok')",
        )

        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        abgebend = page.locator("[data-testid='handover-note-previous']")
        expect(abgebend).to_be_visible()
        expect(abgebend).to_contain_text("E2E-Notiz der abgebenden Schicht")
        expect(abgebend).to_contain_text("E2E-Schicht A")
        laufend = page.locator("[data-testid='handover-note-current']")
        expect(laufend).to_contain_text("E2E-Schicht B")
        assert laufend.locator("textarea").input_value() == ""

    def test_kalenderfall_vortagsnotiz_steht_im_abgebenden_block(self, staff_page, base_url, ohne_zeitfilter, e2e_env):
        """E-11, Kalenderfall: ohne Schichten ist der Vortag die abgebende Schicht.

        Der Tageswechsel selbst laesst sich im Browsergate nicht herbeifuehren
        (die Serverzeit gehoert nicht dem Test). Belegt wird deshalb genau das,
        was die Folgeschicht nach dem Tageswechsel sieht: der ``previous``-Block
        IST der Vortag, er traegt ein Schreibfeld (sein Editierfenster laeuft
        bis zum Ende des heutigen Tages), und was dort geschrieben wird, steht
        nach dem Neuladen wieder im abgebenden Block — nicht im laufenden.
        """
        page = staff_page
        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        abgebend = page.locator("[data-testid='handover-note-previous']")
        expect(abgebend).to_be_visible()

        expect(abgebend).to_contain_text("Übergabenotiz der letzten Schicht")
        _speichern(page, "handover-note-previous", "E2E-Kalendernotiz von gestern")

        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        expect(page.locator("[data-testid='handover-note-previous']")).to_contain_text("E2E-Kalendernotiz von gestern")
        laufend = page.locator("[data-testid='handover-note-current']")
        assert laufend.locator("textarea").input_value() == ""

    def test_speichern_laesst_die_notiz_der_vorschicht_stehen(self, staff_page, base_url, alte_notiz):
        """E-04 im Browser: der HTMX-Austausch darf den Vortext nicht wegraeumen."""
        page = staff_page
        page.goto(
            f"{base_url}/?view=uebergabe&date={VERGANGENES_DATUM}&time_filter={alte_notiz}",
            wait_until="domcontentloaded",
        )
        expect(page.locator("[data-testid='handover-note-previous']")).to_contain_text(
            "E2E-Altnotiz aus dem Fruehdienst"
        )
        _speichern(page, "handover-note-current", "E2E-Notiz waehrend der Anzeige einer Altnotiz")

        container = page.locator("#handover-note")
        expect(container).to_contain_text("E2E-Altnotiz aus dem Fruehdienst")
        expect(container).to_contain_text("E2E-Notiz waehrend der Anzeige einer Altnotiz")
        assert page.locator("[data-testid='handover-note-previous']").count() == 1


class TestSchichtnotizBedienung:
    """a11y-Nachweis am Bedienpfad (WCAG 2.1.1, 2.4.3, 1.3.1)."""

    def test_label_ist_an_das_textfeld_gebunden(self, staff_page, base_url, leere_notizen):
        page = staff_page
        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        textfeld = page.locator("[data-testid='handover-note-current'] textarea")
        expect(textfeld).to_be_visible()
        feld_id = textfeld.get_attribute("id")
        assert page.locator(f"label[for='{feld_id}']").count() == 1

    def test_tastaturweg_vom_textfeld_zum_speichern(self, staff_page, base_url, leere_notizen):
        """Tab führt vom Textfeld direkt auf den Speichern-Knopf (2.1.1)."""
        page = staff_page
        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        textfeld = page.locator("[data-testid='handover-note-current'] textarea")
        textfeld.focus()
        page.keyboard.press("Tab")
        fokus = page.evaluate("() => document.activeElement.textContent.trim()")
        assert fokus == "Notiz speichern"

    def test_fokus_liegt_nach_dem_speichern_wieder_im_textfeld(self, staff_page, base_url, leere_notizen):
        """Nach dem HTMX-Swap kehrt der Fokus an das Ausgangselement zurück (2.4.3)."""
        page = staff_page
        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        _speichern(page, "handover-note-current", "E2E-Fokusprobe")
        # Der Fokus gehoert in das Feld, aus dem gespeichert wurde — nicht in
        # das erste Textfeld der Seite (das waere die abgebende Schicht).
        expect(page.locator("[data-testid='handover-note-current'] textarea")).to_be_focused()

    def test_statusmeldung_wird_hoeflich_angesagt(self, staff_page, base_url, leere_notizen):
        page = staff_page
        page.goto(f"{base_url}/?view=uebergabe", wait_until="domcontentloaded")
        block = page.locator("[data-testid='handover-note-current']")
        block.locator("textarea").fill("E2E-Statusprobe")
        with page.expect_response(lambda r: "/uebergabe/notiz/" in r.url and r.request.method == "POST"):
            block.locator("button[type='submit']").click()
        expect(page.locator("[data-testid='handover-note-current'] textarea")).to_have_value("E2E-Statusprobe")
        status = page.locator("[data-testid='handover-note-status']")
        assert status.get_attribute("aria-live") == "polite"
        expect(status).to_contain_text("Übergabenotiz gespeichert.")
