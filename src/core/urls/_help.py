"""URL patterns for authenticated in-app help (Refs #2043)."""

from django.urls import path

from core.views.help import HelpPageView

urlpatterns = [
    path("hilfe/", HelpPageView.as_view(), name="help_overview"),
    path("hilfe/<slug:slug>/", HelpPageView.as_view(), name="help_page"),
]
