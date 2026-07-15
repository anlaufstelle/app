"""Refs #1496, #1593: Texte des SW-Update-Toasts als data-i18n-sw-*-Attribute.

``sw-register.js`` baut den Update-Toast rein im DOM auf und bezieht seine
Texte ueber ``document.body.dataset`` (Refs #1412 — kein hartkodiertes
deutsches JS-Literal). ``_i18n()`` faellt auf einen **leeren String** zurueck,
wenn das Attribut fehlt: Der Toast rendert dann als lila Kasten mit
beschriftungslosem Button (#1496).

Die Attribute sitzen am ``<body>``. Templates mit **eigenem** ``<body>``
(login.html erbt nicht von base.html) muessen sie daher selbst mitbringen —
sonst ist der Toast dort textlos.

Diese Tests sichern die Verdrahtung fuer jedes Template, das
``sw-register.js`` laedt.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

TOAST_I18N_ATTRS = ("data-i18n-sw-update", "data-i18n-sw-reload", "data-i18n-sw-dismiss")


def _template_dir() -> Path:
    return Path(settings.BASE_DIR) / "templates"


def _templates_loading_sw_register() -> list[Path]:
    """Alle Templates, die sw-register.js einbinden."""
    return sorted(p for p in _template_dir().rglob("*.html") if "sw-register.js" in p.read_text(encoding="utf-8"))


class TestSwToastI18nWiring:
    """Jedes Template mit sw-register.js muss die Toast-Texte bereitstellen."""

    @pytest.mark.django_db
    def test_login_page_renders_toast_i18n_attributes(self, client):
        """#1496: login.html hat ein eigenes <body> und laedt sw-register.js.

        Ohne die Attribute erscheint auf der Login-Seite ein leerer Toast.
        """
        body = client.get(reverse("login")).content.decode()
        missing = [attr for attr in TOAST_I18N_ATTRS if attr not in body]
        assert not missing, f"Login-Seite ohne Toast-Texte (#1496) — fehlende Attribute: {missing}"

    def test_every_template_loading_sw_register_provides_the_texts(self):
        """Struktur-Guard: verhindert die naechste Wiederholung von #1496.

        Ein Template darf sw-register.js nur laden, wenn es die Texte selbst
        setzt ODER von einem Template erbt, das es tut ({% extends %}).
        """
        offenders = []
        for template in _templates_loading_sw_register():
            source = template.read_text(encoding="utf-8")
            inherits = re.search(r"{%\s*extends\s", source) is not None
            provides = all(attr in source for attr in TOAST_I18N_ATTRS)
            if not inherits and not provides:
                offenders.append(template.relative_to(_template_dir()).as_posix())
        assert not offenders, (
            "Template laedt sw-register.js, setzt die data-i18n-sw-*-Texte aber weder "
            f"selbst noch per {{% extends %}} — Toast bliebe textlos (#1496): {offenders}"
        )


class TestSwToastTranslationsEN:
    """EN-msgstr der Toast-Strings (Regression-Lock analog #1144/#1219)."""

    @pytest.mark.parametrize(
        ("de", "en"),
        [
            ("Neue Version verfügbar.", "New version available."),
            ("Neu laden", "Reload"),
        ],
    )
    def test_toast_strings_translated(self, de, en):
        from django.utils.translation import gettext, override

        with override("en"):
            assert gettext(de) == en
