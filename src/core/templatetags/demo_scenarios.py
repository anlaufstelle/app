"""Template-Helfer fuer die Demo-Bereichsauswahl (Refs #2285).

Eigene Datei statt Erweiterung von ``core/context_processors.py``: Letztere
ist NICHT Teil der Besitzliste dieser Lane (TABU-Disziplin, Standardblock
Regel 18/39) — ein Inclusion/Simple-Tag deckt denselben Bedarf (aktueller
Bereich im Demo-Banner der ``base.html``-Shell), ohne eine geteilte, global
wirkende Datei anzufassen, die andere Lanes ebenfalls nutzen.
"""

from django import template
from django.conf import settings

from core.models import User
from core.views import demo_scenarios as _demo_scenario_views

register = template.Library()


@register.simple_tag(takes_context=True)
def demo_current_scenario_name(context):
    """Anzeigename des Demo-Bereichs der/des aktuell angemeldeten Nutzerin.

    Leerer String ausserhalb ``DEMO_MODE``, fuer anonyme Requests und wenn
    (noch) kein Katalogeintrag zur Facility des Nutzers passt (Katalog vor
    dem Merge von F-2/#2279 leer, s. ``core.views.demo_scenarios``).
    """
    if not getattr(settings, "DEMO_MODE", False):
        return ""
    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return ""
    facility = getattr(user, "facility", None)
    if facility is None:
        return ""
    for scenario in _demo_scenario_views.scenario_catalog():
        if scenario.facility_name == facility.name:
            return scenario.display_name
    return ""


@register.filter
def demo_role_label(role_value):
    """Menschlich lesbares Label zu einem ``User.Role``-Wert (fuer Rollen-Radios
    auf der Bereichsauswahl — die Rollen selbst kommen aus dem Katalog als rohe
    ``User.Role``-Werte, nicht als bereits uebersetzte Labels)."""
    return dict(User.Role.choices).get(role_value, role_value)
