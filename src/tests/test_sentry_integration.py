"""Integrationsnachweis für die Sentry-Scrubber-Hooks (Refs #2198).

Die Unit-Guards in ``test_sentry_scrubber.py`` rufen ``before_send`` mit
handgebauten Event-Dicts auf. Dieser Test schließt die verbleibende Lücke: Er
fährt einen **echten** Django-Request auf eine Token-Route durch das **echte**
SDK (``sentry_sdk.init`` mit ``DjangoIntegration``) und prüft, was der Transport
tatsächlich zu sehen bekäme — inklusive der Felder, die das SDK selbst baut
(``request.url`` in der WSGI-Schicht, ``transaction`` im Django-Hook).

Kein Netz, kein DSN: der Transport ist eine In-Memory-Klasse, die Envelopes nur
sammelt. ``sentry_sdk.transport.make_transport`` benutzt eine übergebene
``Transport``-Instanz unabhängig vom DSN, deshalb genügt ``dsn=None``.

Zwei Request-Wege, bewusst getrennt:

* ``WSGIHandler`` — der von der ``DjangoIntegration`` gepatchte Pfad
  (``SentryWsgiMiddleware``). Nur hier füllt das SDK ``request.url``.
* ``django.test.Client`` — fährt über ``BaseHandler._get_response`` und damit
  über ``_before_get_response``, wo der Transaction-Name gesetzt wird. Kein
  Scope-Fork, deshalb ist der Name danach noch am Scope beobachtbar.
"""

import pytest
import sentry_sdk
from django.core.handlers.wsgi import WSGIHandler
from django.test import Client, RequestFactory
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.transport import Transport

from anlaufstelle.settings._sentry import before_breadcrumb, before_send, before_send_transaction
from anlaufstelle.urls import InviteConfirmView

INVITE_UID = "MTIzNDU2Nzg5"
INVITE_TOKEN = "cx1a2b-8f9e0d1c2b3a49586c7d8e9f0a1b2c3d"

# Some supported sentry-sdk/Python combinations emit the
# ``asyncio.iscoroutinefunction`` deprecation while DjangoIntegration is
# active. Keep this exception limited to that exact warning under
# warnings-as-errors; it can be removed once every supported combination uses
# the inspect replacement.
SDK_PY314_DEPRECATION = "ignore:'asyncio.iscoroutinefunction' is deprecated:DeprecationWarning"


class CapturingTransport(Transport):
    """Sammelt Envelopes im Speicher, statt sie zu versenden."""

    def __init__(self):
        super().__init__()
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)

    def serialized(self):
        """Alles, was den Prozess verlassen hätte, als ein einziger String."""
        return "".join(envelope.serialize().decode("utf-8", "replace") for envelope in self.envelopes)


@pytest.fixture
def capturing_sentry():
    """Aktiviert das echte SDK mit unseren Hooks und räumt danach auf.

    ``sentry_sdk.init`` setzt einen prozessweiten Client; der vorherige wird im
    Teardown zurückgesetzt, damit kein Folgetest unter einem aktiven
    Sentry-Client läuft. Die Monkeypatches der Integrationen bleiben im Prozess,
    sind ohne aktiven Client aber wirkungslos — sie fragen selbst
    ``get_client().get_integration(...)`` ab.
    """
    previous = sentry_sdk.get_global_scope().client
    transport = CapturingTransport()
    sentry_sdk.init(
        dsn=None,
        transport=transport,
        before_send=before_send,
        before_send_transaction=before_send_transaction,
        before_breadcrumb=before_breadcrumb,
        # Beide Integrationen laufen in Produktion per Auto-Enabling; die
        # LoggingIntegration ist der Kanal, über den Log-Zeilen zu Breadcrumbs
        # und Events werden (Refs #2198).
        integrations=[DjangoIntegration(), LoggingIntegration()],
        default_integrations=False,
        send_default_pii=False,
        include_local_variables=False,
        traces_sample_rate=0,
    )
    try:
        yield transport
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.get_global_scope().set_client(previous)


def run_through_wsgi(path):
    """Fährt *path* durch den von der DjangoIntegration gepatchten WSGIHandler."""
    environ = RequestFactory().get(path).environ
    seen = {}

    def start_response(status, headers, exc_info=None):
        seen["status"] = status
        return lambda chunk: None

    body = WSGIHandler()(environ, start_response)
    try:
        b"".join(body)
    finally:
        close = getattr(body, "close", None)
        if close is not None:
            close()
    return seen["status"]


@pytest.mark.filterwarnings(SDK_PY314_DEPRECATION)
@pytest.mark.django_db
def test_error_event_from_token_route_carries_no_token(capturing_sentry, monkeypatch):
    """500er auf ``/invite/<uidb64>/<token>/``: kein Token im Envelope.

    ``request.url`` baut die SDK-WSGI-Schicht aus ``scheme://host`` + rohem
    ``PATH_INFO`` — ohne den Fix stünde das live nutzbare Invite-Token
    wortwörtlich im Envelope, zusammen mit dem Referer.
    """

    def boom(self, request, *args, **kwargs):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(InviteConfirmView, "dispatch", boom)

    status = run_through_wsgi(f"/invite/{INVITE_UID}/{INVITE_TOKEN}/")
    sentry_sdk.get_client().flush()

    assert status.startswith("500")
    payload = capturing_sentry.serialized()
    assert payload, "Das SDK muss den 500er als Event erfasst haben."
    assert INVITE_TOKEN not in payload
    # Diagnosewert: die Route bleibt erkennbar.
    assert "invite/<uidb64>/<token>/" in payload


@pytest.mark.filterwarnings(SDK_PY314_DEPRECATION)
@pytest.mark.django_db
def test_event_during_unresolvable_token_path_carries_no_token(capturing_sentry):
    """404 auf einer Token-Route: der Transaction-Name ist die **rohe URL**.

    ``_set_transaction_name_and_source`` fällt bei nicht auflösendem Pfad auf
    ``request.path_info`` zurück (``transaction_info.source == "url"``). Dieser
    Name hängt an jedem Event, das während des Requests entsteht — nicht nur an
    Performance-Transactions.
    """
    status_code = (
        Client(raise_request_exception=False).get(f"/invite/{INVITE_UID}/{INVITE_TOKEN}/nicht-da/").status_code
    )
    sentry_sdk.capture_message("Sonde")
    sentry_sdk.get_client().flush()

    assert status_code == 404
    payload = capturing_sentry.serialized()
    assert payload, "Die Sonden-Message muss als Event erfasst worden sein."
    assert INVITE_TOKEN not in payload


@pytest.mark.filterwarnings(SDK_PY314_DEPRECATION)
def test_log_breadcrumb_extras_never_reach_the_envelope(capturing_sentry):
    """Re-Review 5 / Befund 13, am echten SDK gegengeprüft.

    ``BreadcrumbHandler`` kippt jedes selbst gesetzte ``extra`` in
    ``breadcrumb.data``; ``logger``, ``level`` und ``category`` sind dabei
    **keine** geschützten Namen. Der Guard fährt eine echte Log-Zeile durch die
    echte ``LoggingIntegration`` und prüft das versandfertige Envelope.
    """
    import logging

    facility = "Frauenhaus Musterstadt"
    probe = logging.getLogger("core.tests.sentry_integration_probe")
    probe.addHandler(logging.NullHandler())
    probe.setLevel(logging.WARNING)
    probe.warning(
        "Backend %s uebersprungen (facility=%s)",
        "csv",
        facility,
        extra={"category": facility, "logger": facility, "level": facility, "traeger": facility},
    )
    sentry_sdk.capture_message("Sonde")
    sentry_sdk.get_client().flush()

    payload = capturing_sentry.serialized()
    assert payload, "Die Sonden-Message muss als Event erfasst worden sein."
    assert facility not in payload
    # Diagnosewert: Template und Logger-Name des Records bleiben.
    assert "Backend %s uebersprungen (facility=%s)" in payload
    assert "core.tests.sentry_integration_probe" in payload


@pytest.mark.filterwarnings(SDK_PY314_DEPRECATION)
@pytest.mark.django_db
def test_django_request_logger_is_ignored_by_the_sdk(capturing_sentry):
    """Zusatzkanal ``django.request`` (Akzeptanzkriterium 3), am SDK geprüft.

    ``DjangoIntegration.setup_once`` ruft ``ignore_logger("django.request")``.
    Die „Not Found: /invite/…/<token>/"-Zeile erzeugt deshalb weder Breadcrumb
    noch Event, solange die Integration aktiv ist — der Hook-seitige Pfad-Scrub
    ist für diesen Logger die zweite Verteidigungslinie (und die erste für jeden
    anderen Logger, der einen Pfad mitloggt).
    """
    from sentry_sdk.integrations.logging import _IGNORED_LOGGERS

    Client(raise_request_exception=False).get(f"/invite/{INVITE_UID}/{INVITE_TOKEN}/nicht-da/")
    sentry_sdk.get_client().flush()

    assert "django.request" in _IGNORED_LOGGERS
    assert capturing_sentry.envelopes == []
