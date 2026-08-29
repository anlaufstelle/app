"""Refs #2010: Die Texte des Autosave-Banners kommen aus dem Template-Dataset.

``static/js/autosave.js`` trug drei deutsche Literale — „Entwurf
wiederhergestellt", „Verwerfen" und „Schließen". An einer sonst vollstaendig
uebersetzten Erfassungsmaske las eine englischsprachige Nutzerin damit drei
deutsche Saetze.

Anders als bei ``htmx-errors.js`` (dort eine dokumentierte bewusste Ausnahme:
der Fehlerpfad muss auch dann noch texten koennen, wenn gar kein Template
gerendert wurde) gibt es hier nichts abzuwaegen — ``data-autosave`` steht
ausschliesslich an ``core/events/create.html``, das immer aus einem
Django-Template rendert. Die Texte reisen deshalb ueber das Dataset des
Wurzelelements; dasselbe Muster nutzt ``client-row-offline.js`` seit
Refs #1351/#1385.

Drei Aussagen: die EN-Uebersetzungen stehen im Katalog, das Template liefert
die Attribute in der aktiven Sprache — und die Literale sind aus dem
JavaScript verschwunden (sonst waere die Verdrahtung tot und niemand merkte
es).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils.translation import gettext, override

AUTOSAVE_JS = Path(__file__).resolve().parents[1] / "static" / "js" / "autosave.js"

#: dataset-Attribut -> deutscher msgid.
BANNER_LABELS = {
    "data-i18n-draft-restored": "Entwurf wiederhergestellt",
    "data-i18n-discard": "Verwerfen",
    "data-i18n-close": "Schließen",
    # Refs #2059: derselbe Kanal fuer die Fehlermeldungen des Autosave.
    "data-i18n-autosave-failed": (
        "Der Entwurf konnte nicht zwischengespeichert werden. Bitte den Text sichern und die Seite neu laden."
    ),
    "data-i18n-autosave-quota": (
        "Speicher voll — der Entwurf wird nicht mehr zwischengespeichert. Bitte lokalen Speicher freigeben."
    ),
}


def _autosave_form_tag(content: str) -> str:
    """Der oeffnende ``<form … data-autosave …>``-Tag der Erfassungsmaske."""
    idx = content.index("data-autosave ")
    return content[content.rindex("<form", 0, idx) : content.index(">", idx)]


class TestBannerTranslationsEN:
    """Regression-Lock der EN-msgstr (Muster ``test_i18n_offline_banner.py``)."""

    def test_draft_restored(self):
        with override("en"):
            assert gettext("Entwurf wiederhergestellt") == "Draft restored"

    def test_discard(self):
        with override("en"):
            assert gettext("Verwerfen") == "Discard"

    def test_close(self):
        with override("en"):
            assert gettext("Schließen") == "Close"

    def test_save_failed(self):
        with override("en"):
            assert gettext(BANNER_LABELS["data-i18n-autosave-failed"]) == (
                "The draft could not be saved locally. Please back up your text and reload the page."
            )

    def test_save_quota(self):
        with override("en"):
            assert gettext(BANNER_LABELS["data-i18n-autosave-quota"]) == (
                "Storage is full — the draft is no longer being saved locally. Please free up local storage."
            )


@pytest.mark.django_db
class TestCreateFormCarriesTheLabels:
    """Die Verdrahtung: das ``data-autosave``-Wurzelelement traegt die Texte."""

    def _form_tag(self, client, staff_user) -> str:
        client.force_login(staff_user)
        return _autosave_form_tag(client.get(reverse("core:event_create")).content.decode())

    @pytest.mark.parametrize(("attribute", "german"), BANNER_LABELS.items())
    def test_german_label_is_present(self, client, staff_user, attribute, german):
        assert f'{attribute}="{german}"' in self._form_tag(client, staff_user)

    def test_labels_switch_with_the_active_language(self, client, staff_user):
        # ``preferred_language`` des Kontos gewinnt (core/middleware/user_language.py) —
        # ein ``Accept-Language``-Header allein dreht die Maske nicht.
        staff_user.preferred_language = "en"
        staff_user.save(update_fields=["preferred_language"])
        client.force_login(staff_user)
        tag = _autosave_form_tag(client.get(reverse("core:event_create")).content.decode())

        assert 'data-i18n-draft-restored="Draft restored"' in tag
        assert 'data-i18n-discard="Discard"' in tag
        assert 'data-i18n-close="Close"' in tag


class TestNoGermanLiteralsLeftInTheModule:
    """Ohne diesen Guard bliebe die Verdrahtung unbemerkt wirkungslos."""

    @pytest.mark.parametrize("german", sorted(BANNER_LABELS.values()))
    def test_literal_is_gone_from_autosave_js(self, german):
        source = AUTOSAVE_JS.read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines() if not re.match(r"\s*(//|\*|/\*)", line))

        assert f'"{german}"' not in code, f"{german!r} steht weiterhin als Literal in autosave.js"

    def test_the_dataset_keys_are_actually_read(self):
        source = AUTOSAVE_JS.read_text(encoding="utf-8")

        for key in (
            "i18nDraftRestored",
            "i18nDiscard",
            "i18nClose",
            "i18nAutosaveFailed",
            "i18nAutosaveQuota",
        ):
            assert key in source, f"autosave.js liest {key} nicht aus dem Dataset"
