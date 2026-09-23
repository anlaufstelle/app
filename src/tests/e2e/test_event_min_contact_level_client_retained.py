"""E2E: Personenzuordnung bleibt nach Mindestkontaktstufen-Ablehnung erhalten (Refs #2345).

Reproduziert den Fresh-Install-Befund aus #2173 (Welle N): "Beratungsgespräch"
(Standardkatalog, ``min_contact_stage: qualified``) lehnt eine Person mit der
Kontaktstufe "Nur Pseudonym" ab. Der Unit-Guard
(``test_event_form_min_contact_level_keeps_client.py``) prüft den
gerenderten HTML-Rohtext per Django-Testclient; dieser Browser-Lauf prüft
zusätzlich, dass die Alpine-Autocomplete-Komponente
(``static/js/alpine/forms.js``, ``eventClientAutocomplete``) die vom Server
mitgelieferten ``data-initial-*``-Werte beim Neuaufbau (``init()``) auch
tatsächlich übernimmt und NICHT durch ihre eigene "Typwechsel leert
Auswahl"-Logik (``updateAnonymousAllowed``) sofort wieder verwirft — ein
reiner HTML-/Unit-Test sieht diesen JS-Nebeneffekt nicht (Alpine-CSP-Lücke
von Vitest mit von Hand gestelltem ``$el``).

**Warum der Rohwert per ``fetch`` statt per UI-Formular gepostet wird:**
Die Personensuche filtert serverseitig nach der geforderten Mindeststufe
(``core.views.clients`` ``min_stage``-Parameter, Refs #1803) — bei
ausgewähltem "Beratungsgespräch" liefert die Suche nach "Blitz" (Kontaktstufe
"Nur Pseudonym") deshalb KEINEN Treffer; die Autocomplete-Oberfläche
verhindert genau diese Kombination proaktiv. Die Person wird deshalb zuerst
ganz regulär über die UI bei einem Dokumentationstyp OHNE Mindeststufe
ausgewählt (liefert die echte Personen-UUID), danach wird der exakte
Server-Endpunkt direkt angesprochen — so wie es ein erneut abgeschicktes
Formular täte (z. B. nach Vorwärts-/Zurück-Navigation oder einer
Autosave-Wiederherstellung eines älteren Formularstands): der Server prüft
die Mindest-Kontaktstufe unabhängig vom Autocomplete-Filter (Refs #1160,
``core/services/events/crud.py`` ``_validate_contact_stage``) — das ist die
eigentliche Ablehnung, die dieses Ticket behandelt. Die Serverantwort wird
per ``document.write`` in die laufende Seite geschrieben (anders als
``innerHTML`` führt das eingebettete ``<script>``-Tags tatsächlich aus), damit
Alpine sie wie eine normale Antwort initialisiert.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


class TestEventMinContactLevelClientRetained:
    def test_client_selection_and_hint_survive_the_rejection(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        # Person mit Kontaktstufe "Nur Pseudonym" (Seed-Konstante Blitz-08,
        # src/core/seed/constants.py) regulaer ueber die UI waehlen — bei
        # "Kontakt" gibt es keine Mindeststufe, die Suche ist ungefiltert.
        page.select_option("select[name='document_type']", label="Kontakt")
        page.wait_for_load_state("domcontentloaded")
        autocomplete = page.locator("input[placeholder='Pseudonym eingeben...']")
        autocomplete.fill("Blitz")
        page.locator("#client-autocomplete-list").wait_for(state="visible", timeout=5000)
        # Refs #598 (Muster test_quick_capture.py): Alpine refetched nach dem
        # 150ms-Debounce und rerendert den Dropdown-Inhalt — ohne UI-Signal
        # dafuer ist eine kurze Stabilisierungs-Pause noetig.
        page.wait_for_timeout(300)
        page.locator("#client-autocomplete-list button:has-text('Blitz-08')").wait_for(state="visible", timeout=5000)
        page.locator("#client-autocomplete-list button:has-text('Blitz-08')").click()
        client_id = page.locator("input[name='client']").input_value()
        assert client_id, "Blitz-08 wurde nicht ausgewaehlt — Vorbedingung der Ablehnung fehlt."

        doc_type_id = page.locator("select[name='document_type'] option", has_text="Beratungsgespräch").get_attribute(
            "value"
        )
        assert doc_type_id, "Dokumentationstyp 'Beratungsgespräch' (min_contact_stage=qualified) fehlt im Seed."
        occurred_at = page.locator("input[name='occurred_at']").input_value()
        csrf_token = page.locator("input[name='csrfmiddlewaretoken']").first.input_value()

        result = page.evaluate(
            """
            async ([url, docType, clientId, occurredAt, csrf]) => {
                const params = new URLSearchParams();
                params.set("document_type", docType);
                params.set("client", clientId);
                params.set("occurred_at", occurredAt);
                params.set("csrfmiddlewaretoken", csrf);
                const resp = await fetch(url, {
                    method: "POST",
                    headers: {"Content-Type": "application/x-www-form-urlencoded"},
                    body: params.toString(),
                    credentials: "same-origin",
                });
                return {status: resp.status, text: await resp.text()};
            }
            """,
            [page.url, doc_type_id, client_id, occurred_at, csrf_token],
        )

        assert result["status"] == 200, f"Erwartete 200 (Neu-Rendering), bekam {result['status']}."
        html = result["text"]
        assert "Person muss mindestens die Kontaktstufe" in html
        assert "Personen-Stammdaten" in html
        assert f'data-initial-client-id="{client_id}"' in html
        assert 'data-initial-pseudonym="Blitz-08"' in html

        # Die Antwort tatsaechlich in die laufende Seite schreiben (nicht nur
        # als HTML-Text pruefen) — ``document.write`` fuehrt eingebettete
        # ``<script>``-Tags aus (anders als ``innerHTML``), erst so laeuft
        # Alpines ``init()`` gegen die neuen ``data-initial-*``-Attribute.
        page.evaluate(
            "(html) => { document.open(); document.write(html); document.close(); }",
            html,
        )
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_function("() => window.Alpine !== undefined", timeout=10000)
        # Kein UI-Signal fuer den abgeschlossenen Alpine-init()-Tick verfuegbar.
        page.wait_for_timeout(300)

        # (a) Die Personenzuordnung bleibt sichtbar erhalten — Autocomplete-
        # Textfeld UND das versteckte ``client``-Feld, das Alpine aus
        # ``data-initial-client-id`` re-initialisiert.
        autocomplete_after = page.locator("input[placeholder='Pseudonym eingeben...']")
        assert autocomplete_after.input_value() == "Blitz-08", (
            "Die Autocomplete-Anzeige verliert die Person — die Alpine-Komponente hat "
            "``data-initial-pseudonym`` nicht uebernommen oder sofort wieder geleert."
        )
        client_hidden_value = page.locator("input[name='client']").input_value()
        assert client_hidden_value == client_id, (
            "Das versteckte client-Feld verliert die Zuordnung — "
            "``updateAnonymousAllowed()`` hat die Auswahl beim Neuaufbau (init()) geloescht."
        )

        # Kein wortloser Reset-Hinweis: die (aus dem Server-Kontext restaurierte)
        # Auswahl gilt weiter, sie wurde nicht durch einen Typwechsel entwertet.
        cleared_hint = page.locator("[data-testid='client-selection-cleared']")
        assert cleared_hint.count() == 0 or not cleared_hint.is_visible()

        # (b) Die Meldung nennt Grund UND Weg zur Abhilfe.
        assert page.get_by_text("Person muss mindestens die Kontaktstufe").is_visible()
        assert page.get_by_text("Personen-Stammdaten").is_visible()

        # (c) Ein anderes Eingabefeld (Zeitpunkt) bleibt ebenfalls erhalten (Bestand).
        assert page.locator("input[name='occurred_at']").input_value() == occurred_at
