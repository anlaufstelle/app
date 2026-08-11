"""Die Personensuche in den Formularen schweigt nie mehr (Refs #1803, Konzept §3.8).

Befund R4/R1: Beide Autocomplete-Komponenten setzten `this.show = data.length > 0`.
Null Treffer hiess: das Listenfeld blieb unsichtbar. Kein „keine Treffer", kein
Angebot, und vor allem **kein Unterschied** zu „ich habe noch nicht genug
getippt". Verschärft durch die stille Stufen-Filterung: bei gesetzter
Mindest-Kontaktstufe leerte `updateAnonymousAllowed` sogar eine bereits
getroffene Auswahl — für die Nutzerin verschwand eine Person, die sie eben noch
gesehen hatte, ohne Grund und ohne Hinweis.

Was das Laufzeitverhalten angeht (Leerzustand erscheint, Auswahl wird geleert),
prüft `src/tests/e2e/test_client_autocomplete_empty.py`. Hier steht die
Verdrahtung: dass die Texte überhaupt im Markup ankommen, dass der
„Person anlegen"-Sprung **serverseitig** nach Rolle gegated ist und dass die
Stufen-Erklärung ihr Label vom Server bekommt statt es zu erfinden.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

EMPTY_BOX = 'data-testid="client-autocomplete-empty"'
CREATE_LINK = 'data-testid="client-autocomplete-create"'
NO_MATCH_TEXT = "Keine Person mit diesem Pseudonym gefunden."

# Die drei Formulare mit Personensuche (Konzept §1.6).
FORM_ROUTES = ["core:event_create", "core:workitem_create", "core:case_create"]


def _page(client, user, route):
    client.force_login(user)
    return client.get(reverse(route)).content.decode()


@pytest.mark.django_db
class TestEmptyStateIsWiredOnEveryForm:
    @pytest.mark.parametrize("route", FORM_ROUTES)
    def test_form_carries_the_empty_state_box(self, client, staff_user, route):
        content = _page(client, staff_user, route)

        assert EMPTY_BOX in content, f"{route} hat keinen Leerzustand fuer die Personensuche"
        assert NO_MATCH_TEXT in content, route

    @pytest.mark.parametrize("route", FORM_ROUTES)
    def test_staff_gets_the_create_person_jump(self, client, staff_user, route):
        content = _page(client, staff_user, route)
        idx = content.index(CREATE_LINK)
        link = content[content.rindex("<a", 0, idx) : content.index(">", idx)]

        assert f'href="{reverse("core:client_create")}"' in link, link

    def test_assistant_never_sees_the_create_person_jump(self, client, assistant_user):
        """`core:client_create` ist STAFF_PLUS — ein Link dorthin waere fuer die
        Assistenz ein 403-Sackgassenangebot (und broeche
        `test_authz_template_links`). Das Gate liegt serverseitig, nicht in
        einem `x-show`."""
        content = _page(client, assistant_user, "core:event_create")

        assert EMPTY_BOX in content, "Der Leerzustand selbst gilt fuer alle Rollen"
        assert CREATE_LINK not in content


@pytest.mark.django_db
class TestStageFilteringExplainsItself:
    """R1 — die Regel wird benannt, nie die gefilterte Menge.

    Der Satz kommt aus dem `data-min-stage-label` des gewaehlten `<option>` und
    ist damit serverseitig uebersetzt. Er erklaert die REGEL, nicht das
    Ergebnis: die Existenz einer Person unterhalb der Stufe wird nirgends
    bestaetigt.
    """

    def test_document_type_options_carry_their_stage_label(self, client, staff_user, facility):
        from core.models import DocumentType

        DocumentType.objects.create(
            facility=facility,
            name="Nur fuer qualifizierte Personen",
            sensitivity=DocumentType.Sensitivity.NORMAL,
            min_contact_stage="qualified",
        )
        content = _page(client, staff_user, "core:event_create")
        doc_type = DocumentType.objects.get(name="Nur fuer qualifizierte Personen")

        idx = content.index(f'value="{doc_type.pk}"')
        option = content[content.rindex("<option", 0, idx) : content.index(">", idx)]

        assert 'data-min-stage="qualified"' in option, option
        assert f'data-min-stage-label="{doc_type.get_min_contact_stage_display()}"' in option, option

    def test_the_hint_template_is_translated_server_side(self, client, staff_user):
        content = _page(client, staff_user, "core:event_create")

        assert "data-min-stage-hint=" in content
        assert "sind nur Personen ab der Kontaktstufe" in content

    def test_the_hint_attribute_survives_its_own_quotation_marks(self, client, staff_user):
        """Der Satz enthaelt selbst ein Anfuehrungszeichen — und `{% trans %}`
        escapt seinen Rueckgabewert NICHT.

        Direkt ins Attribut geschrieben riss der Text es auf: alles hinter dem
        inneren `"` wurde zu Pseudo-Attributen, und die Komponente las eine
        abgeschnittene Vorlage. Der Wert muss escaped im Attribut stehen und
        beim Auslesen wieder vollstaendig sein.
        """
        content = _page(client, staff_user, "core:event_create")
        idx = content.index("data-min-stage-hint=")
        value = content[idx + len('data-min-stage-hint="') :]
        value = value[: value.index('"')]

        assert value.endswith("auswählbar."), value
        assert "%(stage)s" in value, value
        assert "&quot;" in value, "Das innere Anfuehrungszeichen muss escaped sein: " + value

    def test_clearing_the_selection_says_so(self, client, staff_user):
        """Bisher war der Reset wortlos."""
        content = _page(client, staff_user, "core:event_create")

        assert 'data-testid="client-selection-cleared"' in content
        assert "Die Personenauswahl wurde zurückgesetzt" in content


class TestEnglishRenderingLock:
    """Refs ADR-027 — die neuen Texte auch auf Englisch festgenagelt."""

    EN = [
        ("Keine Person mit diesem Pseudonym gefunden.", "No person found with this pseudonym."),
        (
            'Für diesen Dokumentationstyp sind nur Personen ab der Kontaktstufe „%(stage)s" auswählbar.',
            'For this documentation type, only people at contact stage "%(stage)s" or above can be selected.',
        ),
        (
            "Die Personenauswahl wurde zurückgesetzt: dieser Dokumentationstyp verlangt eine höhere Kontaktstufe.",
            "The selected person was cleared: this documentation type requires a higher contact stage.",
        ),
    ]

    @pytest.mark.parametrize(("source", "english"), EN, ids=[s[:32] for s, _ in EN])
    def test_english_catalog_renders_the_expected_string(self, source, english):
        from django.utils.translation import gettext, override

        with override("en"):
            assert gettext(source) == english
