"""Refs #2060: Der Hinweis auf einen Versionskonflikt der lokalen Datenbank.

Oeffnet aelterer Code eine neuere IndexedDB (Rollback-Deploy, zweite aeltere
Instanz auf derselben Origin), antwortet der Browser mit einem
``VersionError``. Bis hierher hat den niemand behandelt: alle Store-Operationen
rejecten, die Offline-Daten liegen unerreichbar auf dem Geraet — und die
Nutzerin sieht je nach Aufrufer nichts oder etwas Kryptisches, obwohl der
Zustand trivial heilbar ist.

``offline-db.js`` faengt den Fall jetzt am Oeffnen ab und zeigt einen Toast.
Sein Text kommt ueber denselben ``data-i18n-*``-Kanal am ``<body>`` wie der
Service-Worker-Update-Toast (Refs #1412) — geliefert allerdings von genau EINEM
der fuenf Layouts, die ``offline-db.js`` laden: ``base.html``.

Cross-Review: Ohne Label blieb der Toast stumm, und zwar
ausgerechnet dort, wo die Meldung am noetigsten ist — Anmeldung,
Passwortwechsel, terminaler Offline-Fallback und die precachten In-Place-Shells
(``offline_base.html``, das den ``CACHE_NAME``-Bump braucht). Das Modul traegt
deshalb einen deutschen Rueckfall-Text
als dokumentierte Ausnahme nach dem Muster von ``htmx-errors.js``; das
uebersetzte Template-Label gewinnt, wo es existiert.

Die Tests halten beide Haelften fest: das Label ist da und uebersetzt — UND es
gibt keinen Ladepfad, auf dem die Meldung ausfaellt.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils.translation import gettext, override

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
OFFLINE_DB_JS = Path(__file__).resolve().parents[1] / "static" / "js" / "offline-db.js"
ATTRIBUTE = "data-i18n-db-update-required"
MESSAGE = (
    "Die lokal gespeicherten Daten stammen aus einer neueren Programmversion. "
    "Bitte die Seite neu laden, um die Anwendung zu aktualisieren."
)


class TestTheLayoutCarriesTheLabel:
    def test_body_carries_the_attribute(self):
        source = (TEMPLATES / "base.html").read_text(encoding="utf-8")

        assert ATTRIBUTE in source, "base.html liefert den Hinweistext nicht"

    def test_the_attribute_is_translated_not_hardcoded(self):
        source = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        idx = source.index(ATTRIBUTE)

        assert "{% trans" in source[idx : idx + 40], "Hinweistext ohne trans-Tag"


class TestNoLoadPathStaysSilent:
    """Die Meldung darf auf KEINEM Ladepfad ausfallen.

    Seit dem ``anlaufstelle-v42``-Bump (0.90-Merge-Punkt, O-1) liefern ALLE
    fuenf Layouts das uebersetzte Dataset-Label; der Rueckfall im Modul bleibt
    als Netz fuer kuenftige Ladepfade ohne Label bestehen.
    """

    #: Layouts, die ``offline-db.js`` laden, aber kein Label liefern — seit dem
    #: v42-Bump leer; die Konstante bleibt, damit ein kuenftiges labelloses
    #: Layout hier bewusst eingetragen (und vom Rueckfall getragen) wird.
    WITHOUT_LABEL = ()

    #: Layouts, die das uebersetzte Label liefern.
    WITH_LABEL = (
        "base.html",
        "auth/login.html",
        "auth/password_change.html",
        "offline_base.html",
        "offline.html",
        # Refs #2285: Demo-Bereichsauswahl (Standalone-Dokument, laedt offline-db.js
        # fuer die Warnung vor ungespeicherter Offline-Arbeit).
        "auth/demo_scenario_select.html",
    )

    def test_every_layout_loading_offline_db_is_accounted_for(self):
        """Kommt ein sechstes Layout dazu, faellt dieser Test — nicht der Toast."""
        loading = {
            str(path.relative_to(TEMPLATES))
            for path in TEMPLATES.rglob("*.html")
            if "offline-db.js" in path.read_text(encoding="utf-8")
        }

        assert loading == {*self.WITH_LABEL, *self.WITHOUT_LABEL}

    @pytest.mark.parametrize("template", WITHOUT_LABEL)
    def test_these_layouts_really_have_no_label(self, template):
        """Die Praemisse des Rueckfalls — sonst prueft er nichts."""
        source = (TEMPLATES / template).read_text(encoding="utf-8")

        assert ATTRIBUTE not in source

    @pytest.mark.parametrize("template", WITH_LABEL)
    def test_the_non_precached_layouts_carry_the_translated_label(self, template):
        """Refs #2060 (Nachrunde): Wo kein Precache-Bump noetig ist, wird uebersetzt.

        Der Rueckfall bleibt genau dort der Regelfall, wo das Attribut einen
        ``CACHE_NAME``-Bump kosten wuerde — nicht ueberall sonst.
        """
        source = (TEMPLATES / template).read_text(encoding="utf-8")
        idx = source.index(ATTRIBUTE)

        assert "{% trans" in source[idx : idx + 40], f"{template}: Hinweistext ohne trans-Tag"

    def test_the_module_carries_a_german_fallback(self):
        source = OFFLINE_DB_JS.read_text(encoding="utf-8")

        assert "UPDATE_REQUIRED_FALLBACK" in source
        assert "if (!label) return;" not in source, "Der Toast bricht wieder ohne Label ab"

    def test_the_fallback_is_wordidentical_with_the_translated_message(self):
        """Sonst driften Rueckfall und Katalog auseinander.

        Der Rueckfall steht im JS als umbrochene String-Konkatenation; hier
        wird das Konkatenat rekonstruiert und mit dem msgid verglichen.
        """
        source = OFFLINE_DB_JS.read_text(encoding="utf-8")
        block = source[source.index("const UPDATE_REQUIRED_FALLBACK") :]
        block = block[: block.index(";")]
        literal = "".join(re.findall(r'"([^"]*)"', block))

        assert literal == MESSAGE

    def test_the_dismiss_label_has_a_fallback_too(self):
        """``data-i18n-sw-dismiss`` fehlt in ``password_change.html`` und ``offline.html``."""
        source = OFFLINE_DB_JS.read_text(encoding="utf-8")

        assert "DISMISS_FALLBACK" in source


class TestTranslationEN:
    def test_message_is_translated(self):
        with override("en"):
            assert gettext(MESSAGE) == (
                "The locally stored data comes from a newer program version. "
                "Please reload the page to update the application."
            )


@pytest.mark.django_db
class TestRenderedPageCarriesTheLabel:
    def test_a_normal_page_renders_the_message(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:client_list")).content.decode()

        assert f'{ATTRIBUTE}="{MESSAGE}"' in content

    def test_the_login_page_renders_it_too(self, client):
        """Refs #2060 (Nachrunde): der anonyme Einstieg laedt offline-db.js.

        Statisch geprueft wird das Attribut oben; hier wird es tatsaechlich
        gerendert — die Anmeldeseite erbt nicht von ``base.html``.
        """
        content = client.get(reverse("login")).content.decode()

        assert f'{ATTRIBUTE}="{MESSAGE}"' in content


class TestModuleWiring:
    """Ohne das Auslesen im Modul waere das Attribut totes Markup."""

    def test_offline_db_reads_the_dataset_key(self):
        source = OFFLINE_DB_JS.read_text(encoding="utf-8")

        assert "i18nDbUpdateRequired" in source
        assert "VersionError" in source
