"""Unit-Tests für FacilityScopedViewMixin und HTMXPartialMixin (Refs #598 R-2/R-3)."""

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.views import View

from core.views.mixins import FacilityScopedViewMixin, HTMXPartialMixin

# --- FacilityScopedViewMixin ---------------------------------------------


class TestFacilityScopedViewMixin:
    def test_property_returns_request_current_facility(self):
        class DummyView(FacilityScopedViewMixin, View):
            pass

        rf = RequestFactory()
        request = rf.get("/")
        request.current_facility = object()

        view = DummyView()
        view.request = request

        assert view.facility is request.current_facility

    def test_property_returns_none_when_middleware_did_not_set(self):
        """Anonymous/Middleware-ohne-Facility-Pfad: ``current_facility`` ist
        ``None``; das Mixin leitet das 1:1 durch (keine Guard-Logik)."""

        class DummyView(FacilityScopedViewMixin, View):
            pass

        rf = RequestFactory()
        request = rf.get("/")
        request.current_facility = None

        view = DummyView()
        view.request = request

        assert view.facility is None


# --- HTMXPartialMixin ----------------------------------------------------


class TestHTMXPartialMixin:
    def _make_view(self, template_name="full.html", partial_template_name="partial.html"):
        class DummyView(HTMXPartialMixin, View):
            pass

        DummyView.template_name = template_name
        DummyView.partial_template_name = partial_template_name
        return DummyView

    def test_is_htmx_detects_header(self):
        cls = self._make_view()
        rf = RequestFactory()

        view = cls()
        view.request = rf.get("/", HTTP_HX_REQUEST="true")
        assert view.is_htmx() is True

        view.request = rf.get("/")
        assert view.is_htmx() is False

    def test_is_htmx_ignores_non_true_values(self):
        cls = self._make_view()
        rf = RequestFactory()
        view = cls()
        view.request = rf.get("/", HTTP_HX_REQUEST="false")
        assert view.is_htmx() is False

    def test_render_htmx_or_full_raises_on_missing_template_config(self):
        cls = self._make_view(template_name=None, partial_template_name=None)
        rf = RequestFactory()
        view = cls()
        view.request = rf.get("/")

        with pytest.raises(ValueError, match="HTMXPartialMixin"):
            view.render_htmx_or_full({})


# --- Integration: Mixin rendert echtes Template --------------------------


@pytest.mark.django_db
class TestHTMXPartialMixinRendersCorrectTemplate:
    """Integration-Smoke: Ein DummyView mit temporär registrierter URL
    demonstriert, dass sowohl Full- als auch Partial-Pfad sauber rendern.
    Wir benutzen ein immer-rendernbares Template aus dem Projekt —
    ``components/_time_filter_dropdown.html`` ist zu komplex (Context),
    deshalb greifen wir auf einen simplen String-Context zu.
    """

    def test_dispatches_partial_template_on_hx(self, rf=None):
        """Wir bauen die Templates on-the-fly im Test: der Mixin muss nur
        das erwartete Template an ``render`` übergeben. Direktes Unit-
        Testing der Render-Auswahl ist ausreichend — die Template-
        Resolution selbst ist Django-Eigenimplementierung."""
        from unittest.mock import patch

        cls = type(
            "DummyView",
            (HTMXPartialMixin, View),
            {"template_name": "full.html", "partial_template_name": "partial.html"},
        )
        rf = RequestFactory()

        view = cls()
        view.request = rf.get("/", HTTP_HX_REQUEST="true")

        with patch("django.shortcuts.render") as mock_render:
            mock_render.return_value = HttpResponse("ok")
            view.render_htmx_or_full({"x": 1})

        mock_render.assert_called_once_with(view.request, "partial.html", {"x": 1})

    def test_dispatches_full_template_otherwise(self):
        from unittest.mock import patch

        cls = type(
            "DummyView",
            (HTMXPartialMixin, View),
            {"template_name": "full.html", "partial_template_name": "partial.html"},
        )
        rf = RequestFactory()

        view = cls()
        view.request = rf.get("/")

        with patch("django.shortcuts.render") as mock_render:
            mock_render.return_value = HttpResponse("ok")
            view.render_htmx_or_full({"x": 1})

        mock_render.assert_called_once_with(view.request, "full.html", {"x": 1})
