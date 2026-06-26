"""Unit-Tests für die wiederverwendbaren Template-Komponenten (Refs #1016, C5).

Render-Ebene: prüft, dass ``components/_form_field.html`` (Label + aria_field +
Help-/Error-Markup) korrekt in die refaktorierten Formulare eingebunden ist —
inkl. Pflichtfeld-Stern (über ``field.field.required``) und Fehler-Liste.
"""

import pytest
from django.urls import reverse

_LABEL_CLASS = 'class="block text-[12px] font-bold uppercase tracking-[0.05em] text-ink-soft mb-1.5"'


@pytest.mark.django_db
class TestFormFieldComponent:
    def test_client_form_renders_field_component(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:client_create")).content.decode()
        assert _LABEL_CLASS in content
        # aria_field setzt aria-required auf Pflichtfeldern.
        assert 'aria-required="true"' in content

    def test_case_form_renders_required_star(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:case_create")).content.decode()
        assert _LABEL_CLASS in content
        # Titel ist Pflicht → Partial rendert das Sternchen.
        assert " *" in content

    def test_workitem_form_renders_field_component(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:workitem_create")).content.decode()
        assert _LABEL_CLASS in content

    def test_field_component_renders_error_list(self, client, staff_user):
        """Invalider POST → Fehler-Liste (id_X-error, role=alert) aus dem Partial."""
        client.force_login(staff_user)
        resp = client.post(reverse("core:case_create"), {"title": "", "description": ""})
        content = resp.content.decode()
        assert 'id="id_title-error"' in content
        assert 'role="alert"' in content


class TestBadgeComponent:
    """C5: components/_badge.html (Basis .badge + Farb-Variante) + status_badge-Tag."""

    def test_badge_component_renders_class_color_text(self):
        from django.template.loader import render_to_string

        html = render_to_string("components/_badge.html", {"text": "Dringend", "color": "red", "title": "Hinweis"})
        assert 'class="badge bg-red-100 text-red-800"' in html
        assert 'title="Hinweis"' in html
        assert ">Dringend<" in html

    def test_badge_component_omits_title_when_absent(self):
        from django.template.loader import render_to_string

        html = render_to_string("components/_badge.html", {"text": "X", "color": "green"})
        assert "title=" not in html

    def test_status_badge_uses_badge_component_unescaped(self):
        """status_badge muss echtes HTML liefern (nicht escaped) — Single Source via _badge.html."""
        from django.template import Context, Template

        out = Template('{% load core_tags %}{% status_badge "open" "Offen" %}').render(Context({}))
        assert '<span class="badge ' in out
        assert "&lt;span" not in out
        assert "bg-green-100" in out
        assert ">Offen<" in out


class TestHighlightsToggleComponent:
    """Refs #1286: Übergabe-Highlights — ganze Kopfzeile togglet (Button), Aufgaben verlinken.

    Render-Ebene (kein DB nötig): prüft die beiden konditionalen Zweige von
    ``core/handover/partials/_highlights.html`` deterministisch — den Krisen/Hausverbot-
    Button-Zweig und den Aufgaben-Link-Zweig. Live-Toggle-Verhalten deckt der E2E-Test
    ``TestFeedCardHeaderToggle`` ab (gleiche ``expandableCard``-Mechanik).
    """

    @staticmethod
    def _render(highlight):
        from types import SimpleNamespace

        from django.template.loader import render_to_string

        return render_to_string(
            "core/handover/partials/_highlights.html",
            {"summary": SimpleNamespace(highlights=[highlight])},
        )

    def test_crisis_header_is_button_toggle(self):
        from datetime import time
        from types import SimpleNamespace

        pk = "929c8152-d514-4979-bb01-c5bc9e56dbc4"
        crisis = SimpleNamespace(
            type="crisis",
            time=time(8, 0),
            object=SimpleNamespace(
                pk=pk,
                client=SimpleNamespace(pseudonym="Klient"),
                preview_fields=[{"label": "Dauer", "value": "30 min", "is_textarea": False}],
                expanded_fields=[{"label": "Verlauf", "value": "…", "is_textarea": True}],
            ),
        )
        html = self._render(crisis)
        # Ganze Kopfzeile = <button>, das auf das Detail-Panel verweist.
        assert f'aria-controls="highlight-detail-{pk}"' in html
        assert f'id="highlight-detail-{pk}"' in html
        assert ':aria-expanded="expanded"' in html
        # Button enthält keinen verschachtelten <div> (gültiges HTML, button = Phrasing-Content).
        header = html.split("<button", 1)[1].split("</button>", 1)[0]
        assert "<div" not in header
        # "Zum Eintrag"-Link bleibt erhalten (außerhalb des Buttons).
        assert f"/events/{pk}/" in html

    def test_task_is_link_not_toggle(self):
        from datetime import time
        from types import SimpleNamespace

        pk = "01367217-3d2c-4c56-bd9d-5bcd5d1c70e9"
        task = SimpleNamespace(
            type="task",
            time=time(9, 0),
            object=SimpleNamespace(
                pk=pk,
                title="Streetwork-Bericht",
                assigned_to=SimpleNamespace(display_name="Max Muster", username="max"),
            ),
        )
        html = self._render(task)
        # Aufgabe verlinkt zur WorkItem-Detailseite (vorher gar nicht klickbar).
        assert f'href="/workitems/{pk}/"' in html
        # Aufgaben sind nicht aufklappbar -> kein Toggle-Button.
        assert "<button" not in html
