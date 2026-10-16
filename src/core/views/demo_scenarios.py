"""Demo-Bereichsauswahl und Bereichswechsel (Refs #2285).

Zwei Views, beide ausschliesslich bei ``DEMO_MODE`` erreichbar (Ticket-
Punkt 7) — ausserhalb liefern sie 404, dieselbe Doktrin wie
``DemoImprintView``/``DemoPrivacyView`` (``core/views/legal.py``): die Route
existiert im URLconf immer (stabile ``reverse()``-Namen fuer Templates/Tests),
verhaelt sich ausserhalb der Demo aber wie eine nicht existierende Seite.

* ``DemoScenarioSelectView`` (GET, oeffentlich): zeigt die Karten des
  fachlichen Szenariokatalogs (``core.seed.scenarios``, Refs #2279) und
  markiert nicht bereitgestellte/archivierte Bereiche als nicht verfuegbar.
  Ein ``?bereich=<key>``-Deep-Link waehlt NUR vor (Ticket-Punkt 8) — er
  fokussiert die Karte, loest aber keinen Login aus.
* ``DemoScenarioStartView`` (POST, CSRF-geschuetzt): validiert Szenario- und
  Rollen-Schluessel serverseitig gegen den Katalog (kein frei waehlbares
  Konto aus Request-Parametern, Ticket-Punkt 7) und meldet die Sitzung per
  ``django.contrib.auth.login`` auf dem fest zugeordneten Demo-Konto an.
  Django cycelt/flusht dabei die Session (``login()``: neuer User-Pk -> ``
  session.flush()``) — ein Bereichswechsel verliert damit automatisch die
  alte Sitzung, OHNE eine ``facility``-Zuordnung umzuschreiben (Ticket-Punkt
  5/9). Das Weiterleitungsziel ist fest ``settings.LOGIN_REDIRECT_URL`` — kein
  ``next``-Parameter wird ausgewertet (Ticket-Punkt 8, „auf vorgesehene
  Demo-Seiten begrenzen").

Bewusst KEIN Import von ``core.seed.scenarios`` auf Modulebene: der
Katalog (#2279) entstand parallel zu dieser Auswahl und wird NUR über die
dort dokumentierte Schnittstelle konsumiert (keine eigene Kopie).
``scenario_catalog()``/``_lookup_scenario()`` importieren daher
weiterhin lazy zur Aufrufzeit und fangen ein fehlendes Modul ab — der
Katalog liegt seit dem Rebase auf F-2s Merge-Stand jetzt im Baum, der
Lazy-Import bleibt trotzdem stehen (kostet nichts, schützt vor einem
künftigen erneuten Auseinanderlaufen zwischen parallelen Aenderungen).

Facility-/Konto-Lookup liegt in ``core.services.demo_scenarios``
(``facility_for_scenario``/``account_for``): ``TestTenantModelScopeGuard``
verlangt Facility-Scope-Evidenz für JEDEN ``<Model>.objects``-Zugriff in
``core/views/**`` — dieser View-Layer fasst daher keine facility-tragenden
Modelle mehr direkt an.
"""

from __future__ import annotations

from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.http import require_http_methods
from django_ratelimit import UNSAFE

from core.constants import RATELIMIT_FREQUENT
from core.demo import DEMO_OFFLINE_BOOTSTRAP_ERROR, DEMO_OFFLINE_BOOTSTRAP_PENDING, DEMO_PASSWORD
from core.middleware.session_idle import ABSOLUTE_DEADLINE_KEY, LAST_RENEWAL_KEY
from core.models import User
from core.models.settings import DEFAULT_SESSION_ABSOLUTE_HOURS
from core.services.demo_scenarios import account_for, facility_for_scenario
from core.utils.ratelimit import ratelimit

#: Refs #573/#1065 (``CustomLogoutView``, ``core/views/auth.py``, dort als
#: Vorbild; die bestehende Abmeldung bleibt unveraendert). Gleicher Wert wie
#: beim Logout:
#: "cookies" bleibt bewusst draussen, die neue Session-Cookie dieser Antwort
#: soll tragen.
CLEAR_SITE_DATA_VALUE = '"storage", "cache"'

SCENARIO_SELECT_URL_NAME = "demo_scenario_select"


def scenario_catalog():
    """Der Szenario-Katalog aus F-2 (#2279) — leer, solange nicht gemerged."""
    try:
        from core.seed.scenarios import SCENARIOS
    except ModuleNotFoundError:
        return ()
    return SCENARIOS


def _lookup_scenario(key):
    """Ein Szenario per Schluessel, ``None`` bei unbekanntem/fehlendem Katalog."""
    if not key:
        return None
    try:
        from core.seed.scenarios import get_scenario
    except ModuleNotFoundError:
        return None
    try:
        return get_scenario(key)
    except KeyError:
        return None


def _demo_mode_or_404():
    if not getattr(settings, "DEMO_MODE", False):
        raise Http404


def require_demo_mode(view):
    """Keep disabled demos at 404 before method filters or quotas (Refs #2350, #2529)."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        _demo_mode_or_404()
        return view(request, *args, **kwargs)

    return wrapper


def _default_role(scenario):
    """Alltagsrolle als Voreinstellung (Ticket-Punkt 4): STAFF, sofern das
    Szenario sie anbietet, sonst die erste im Katalog hinterlegte Rolle
    (Katalogreihenfolge ist bewusst die "naheliegendste zuerst").

    ``None`` bei einem Katalogeintrag OHNE Rollen (Datenfehler des Katalogs) —
    fail-closed statt ``IndexError`` im heissen Pfad. Ein ``None``-Rueckgabewert
    macht die Karte in :func:`scenario_cards`
    nicht verfuegbar und laesst ``DemoScenarioStartView.post`` denselben,
    bereits vorhandenen "Rolle nicht freigegeben"-Zweig nehmen (``None not
    in scenario.demo_roles`` ist bei leerem Tupel immer wahr) — kein
    Sonderfall im Start-Pfad noetig."""
    if not scenario.demo_roles:
        return None
    if User.Role.STAFF in scenario.demo_roles:
        return User.Role.STAFF
    return scenario.demo_roles[0]


def scenario_cards(current_facility_id=None):
    """Anzeige-Datensaetze fuer die Auswahlseite, EINE Quelle fuer GET+POST."""
    cards = []
    for scenario in scenario_catalog():
        facility, available = facility_for_scenario(scenario)
        default_role = _default_role(scenario)
        cards.append(
            {
                "scenario": scenario,
                "available": available and default_role is not None,
                "is_current": bool(facility and current_facility_id and facility.pk == current_facility_id),
                "default_role": default_role,
                "other_roles": tuple(role for role in scenario.demo_roles if role != default_role),
            }
        )
    return cards


def _stamp_session_timeout(request, facility):
    """Facility-Sitzungsfenster + absolutes Maximum stempeln.

    Dupliziert bewusst den engen Ausschnitt aus ``CustomLoginView.form_valid``
    (``core/views/auth.py``, dort Vorbild fuer die Abmelde-/Bereinigungs-
    logik — hier fuer den analogen Login-Bootstrap ohne
    Passwortformular). Ohne den Stempel wuerde
    ``SessionIdleRefreshMiddleware`` beim naechsten Request ohnehin einen
    Default-Wert nachtragen (kein harter Fehler) — mit ihm gilt ab dem ersten
    Request sofort das facility-spezifische Fenster, identisch zum
    Passwort-Login.
    """
    from django.utils import timezone

    absolute_hours = DEFAULT_SESSION_ABSOLUTE_HOURS
    if facility is not None:
        try:
            facility_settings = facility.settings
            timeout = facility_settings.session_timeout_minutes * 60
            request.session.set_expiry(timeout)
            absolute_hours = facility_settings.session_absolute_hours
        except facility._meta.model.settings.RelatedObjectDoesNotExist:
            pass
    now = timezone.now().timestamp()
    request.session[LAST_RENEWAL_KEY] = now
    request.session[ABSOLUTE_DEADLINE_KEY] = now + absolute_hours * 3600
    # MFA bleibt in DEMO_MODE global ausgeschaltet (MFA_ENFORCE_PRIVILEGED_ROLES
    # = False, settings/demo.py) — dieses Flag ist damit wirkungslos, wird aber
    # aus Konsistenz zum normalen Login-Session-Zuschnitt gesetzt.
    request.session["mfa_verified"] = False


@method_decorator(require_demo_mode, name="dispatch")
@method_decorator(require_http_methods(["GET", "HEAD", "OPTIONS"]), name="dispatch")
class DemoScenarioSelectView(View):
    """GET /demo/ — Bereichsauswahl (Ticket-Punkt 1/2/8)."""

    template_name = "auth/demo_scenario_select.html"

    def get(self, request):
        preselect_scenario = _lookup_scenario(request.GET.get("bereich", ""))
        current_facility_id = None
        if request.user.is_authenticated:
            current_facility_id = getattr(getattr(request.user, "facility", None), "pk", None)
        context = {
            "scenario_cards": scenario_cards(current_facility_id),
            "preselect_key": preselect_scenario.key if preselect_scenario else "",
        }
        return render(request, self.template_name, context)


@method_decorator(require_demo_mode, name="dispatch")
@method_decorator(
    ratelimit(
        key="ip",
        rate=RATELIMIT_FREQUENT,
        method=UNSAFE,
        block=True,
        group="core.views.demo_scenarios.DemoScenarioStartView.post",
    ),
    name="dispatch",
)
class DemoScenarioStartView(View):
    """POST /demo/start/ — Demo-Start bzw. Bereichs-/Rollenwechsel.

    GET existiert nur als harmloser Redirect zurueck zur Auswahl (kein Login
    per GET/Deep-Link, Ticket-Punkt 8).
    """

    def get(self, request):
        return redirect(SCENARIO_SELECT_URL_NAME)

    def post(self, request):
        scenario = _lookup_scenario(request.POST.get("scenario", ""))
        if scenario is None:
            messages.error(request, _("Dieser Demo-Bereich ist nicht verfügbar."))
            return redirect(SCENARIO_SELECT_URL_NAME)

        role = request.POST.get("role") or _default_role(scenario)
        # Serverseitige Pruefung gegen den Katalog (Ticket-Punkt 7): kein frei
        # waehlbares Konto aus Request-Parametern. ``role`` muss eine der fuer
        # DIESES Szenario tatsaechlich angelegten Rollen sein.
        if role not in scenario.demo_roles:
            messages.error(request, _("Diese Rolle ist für diesen Demo-Bereich nicht freigegeben."))
            select_url = reverse(SCENARIO_SELECT_URL_NAME)
            return redirect(f"{select_url}?bereich={scenario.key}")

        facility, available = facility_for_scenario(scenario)
        if not available:
            messages.error(
                request,
                _(
                    "Dieser Demo-Bereich ist gerade nicht verfügbar (Reset läuft "
                    "oder wird vorbereitet). Bitte gleich erneut versuchen."
                ),
            )
            return redirect(SCENARIO_SELECT_URL_NAME)

        # Fest zugeordnetes Demo-Konto je Facility+Rolle (Ticket-Punkt 5):
        # NIE die facility-Zuordnung eines Kontos umschreiben, nur ein
        # bestehendes, zu Szenario+Rolle passendes Konto anmelden. Lookup
        # ausgelagert in core.services.demo_scenarios (# TestTenantModelScopeGuard) — deterministisch, kein Konto aus
        # Request-Parametern.
        user = account_for(facility, role)
        if user is None:
            messages.error(request, _("Für diesen Demo-Bereich ist noch kein passendes Konto vorbereitet."))
            return redirect(SCENARIO_SELECT_URL_NAME)

        # Refuse before login and Clear-Site-Data to preserve existing work.
        if not user.check_password(DEMO_PASSWORD):
            messages.error(request, DEMO_OFFLINE_BOOTSTRAP_ERROR)
            return redirect(SCENARIO_SELECT_URL_NAME)

        # ``authenticate()`` entfaellt bewusst — es gibt kein Passwort in
        # diesem Ablauf, Vertrauensanker ist die serverseitige Szenario-/
        # Rollen-Pruefung oben. ``login()`` erkennt einen Wechsel des
        # angemeldeten Nutzers selbst (anderer User-Pk als die evtl. schon
        # laufende Demo-Sitzung) und flusht/cycelt die Session dafuer intern
        # — eine alte Sitzung ueberlebt den Wechsel nicht (Ticket-Punkt 9).
        user.backend = "django.contrib.auth.backends.ModelBackend"
        auth_login(request, user)
        _stamp_session_timeout(request, facility)
        request.session[DEMO_OFFLINE_BOOTSTRAP_PENDING] = True

        response = redirect(settings.LOGIN_REDIRECT_URL)
        response["Clear-Site-Data"] = CLEAR_SITE_DATA_VALUE
        return response


@method_decorator(require_demo_mode, name="dispatch")
class DemoOfflineKeyReadyView(LoginRequiredMixin, View):
    """Confirm browser key preparation without changing the offline salt."""

    @method_decorator(ratelimit(key="user", rate="10/m", method="POST", block=True))
    def post(self, request, *args, **kwargs):
        request.session.pop(DEMO_OFFLINE_BOOTSTRAP_PENDING, None)
        return HttpResponse(status=204)
