"""Sweep-Guard: KEIN Endpunkt antwortet einem einrichtungslosen Konto mit 5xx (Refs #2090).

Die Kernzusage des Tickets ist nicht „diese drei Seiten sind repariert", sondern
„**kein** Endpunkt laeuft mehr in den Serverfehler". Ein handgepflegter Sweep
haette dieselbe Halbwertszeit wie jede zweite Liste im Repo — deshalb liest
dieses Modul dieselbe Soll-Quelle wie die AuthZ-Matrix
(``_authz_expectations.EXPECTATIONS``, 203 Eintraege / 257 Methodenzeilen). Das
Vollstaendigkeits-Gate ``test_authz_matrix.py::test_every_named_pattern_is_declared``
erzwingt fuer jeden neuen benannten URL-Pattern einen Eintrag dort — ein neuer
View wird hier also **automatisch** mitgeprueft, ohne dass eine zweite Liste
gepflegt werden muesste. Genau das ist der vom Maintainer verlangte
„Guard-Test, der neue Views automatisch mitnimmt".

**Eigenes Modul statt sechster Akteur in ``test_authz_matrix.ACTORS``.**
``ACTORS`` wird von vier weiteren Konsumenten gelesen (``test_authz_idor.py``,
``test_authz_template_links.py``, ``test_navigation_backlink.py``,
``e2e/test_authz_audit.py``), und ``test_authz_cell`` zieht seine
Erlaubnis-Entscheidung aus ``exp.methods`` — einen Akteur, der in keiner
Rollenmenge steht, kennt diese Logik strukturell nicht.

**Die Erwartung wird abgeleitet, nicht gepflegt:**

* immer: ``status < 500`` — die Zusage des Tickets;
* ``public``/``auth-flow`` (die beiden Kategorien, aus denen sich
  ``FACILITY_MISSING_EXEMPT_URLS`` herleitet): **derselbe Status wie fuer den
  Referenz-Akteur** ``admin_user`` (``facility_admin`` MIT Einrichtung). Der
  Referenzvergleich erschlaegt die Sonderfaelle von selbst — die
  ``otp_webauthn``-Zeilen (403 fuer jeden), ``extra_ok=(404,)`` bei den
  Demo-Rechtstexten und der Lockout-Bestaetigung, der 301 von ``/uebergabe/``
  und die Rollenweiche von ``/start/`` fallen ohne eine einzige gepflegte
  Ausnahme richtig heraus;
* ``system``: 403 — der Pfad steht bewusst NICHT auf der Whitelist, das
  Rollen-Gate ``SuperAdminRequiredMixin`` antwortet;
* alle uebrigen elf Kategorien: 403 oder 404 — der Wurzel-Guard hat den
  Request beendet, bevor eine View einen Facility-Kontext gebraucht haette.

Refs #2090, https://github.com/anlaufstelle/app/issues/2090.
"""

import pytest
from django.test import Client
from django.urls import reverse

from tests._authz_expectations import EXPECTATIONS
from tests._rbac_helpers import resolve_fixture_kwargs

#: Kategorien, deren Routen ueber ``FACILITY_MISSING_EXEMPT_URLS`` erreichbar
#: bleiben — und die deshalb gegen den Referenz-Akteur verglichen werden.
EXEMPT_CATEGORIES = frozenset({"public", "auth-flow"})

# Bekannte, als Issue dokumentierte Luecken: (url_name, METHODE) -> Begruendung
# mit Issue-URL. Muster wie ``test_authz_matrix.KNOWN_GAPS`` — ``strict=True``,
# damit ein stillschweigend behobener Eintrag auffaellt. Eine 5xx-Zeile in
# ``public``/``auth-flow`` waere ein Defekt unter ``views/``/``services/`` und
# damit ausserhalb dieser Lane; sie gehoert hierher UND in ein Folge-Ticket,
# NICHT in eine abgeschwaechte Assertion.
KNOWN_FACILITY_LESS_GAPS: dict[tuple[str, str], str] = {}


def test_known_gaps_reference_existing_cells():
    """Tippfehler in den Gap-Keys duerfen nicht lautlos ins Leere zeigen."""
    valid = {(exp.url_name, method) for exp in EXPECTATIONS for method, _ in exp.methods}
    stray = set(KNOWN_FACILITY_LESS_GAPS) - valid
    assert not stray, f"KNOWN_FACILITY_LESS_GAPS ohne Zelle: {sorted(stray)}"


def _cells():
    for exp in EXPECTATIONS:
        for method, _allowed in exp.methods:
            marks = []
            if (exp.url_name, method) in KNOWN_FACILITY_LESS_GAPS:
                marks.append(pytest.mark.xfail(reason=KNOWN_FACILITY_LESS_GAPS[(exp.url_name, method)], strict=True))
            yield pytest.param(exp, method, id=f"{exp.url_name}-{method}", marks=marks)


@pytest.mark.django_db
@pytest.mark.parametrize("exp,method", list(_cells()))
def test_facility_less_account_never_sees_a_server_error(client, exp, method, request, facility_less_admin):
    """Eine Sweep-Zelle: der einrichtungslose Nicht-Superadmin auf einem Endpunkt."""
    kwargs = resolve_fixture_kwargs(exp.url_kwargs, request)
    url = reverse(exp.url_name, kwargs=kwargs or None)
    client.force_login(facility_less_admin)

    response = getattr(client, method.lower())(url)
    status = response.status_code

    assert status < 500, f"{url} [{method}]: einrichtungsloses Konto bekam Serverfehler {status}"

    if exp.category in EXEMPT_CATEGORIES:
        # Wer anonym oder mit jeder Rolle auf eine Seite darf, darf ohne
        # Einrichtungsbindung nicht schlechter gestellt sein. Eigener Client,
        # damit die Sitzung des Referenz-Akteurs die erste nicht beeinflusst.
        reference_client = Client()
        reference_client.force_login(request.getfixturevalue("admin_user"))
        reference_status = getattr(reference_client, method.lower())(url).status_code
        assert status == reference_status, (
            f"{url} [{method}]: einrichtungslos {status}, Referenz (mit Einrichtung) {reference_status} — "
            "die Whitelist deckt diese Route nicht (oder zu breit)"
        )
    elif exp.category == "system":
        assert status == 403, f"{url} [{method}]: Systembereich erwartet 403 aus dem Rollen-Gate, bekam {status}"
    else:
        assert status in (403, 404), f"{url} [{method}]: erwartet 403/404, bekam {status}"
