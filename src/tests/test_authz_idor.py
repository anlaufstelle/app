"""Horizontale Mandantentrennung: IDOR-Probes mit Objekten der zweiten Facility (Refs #1055).

Akteur ist die jeweils STÄRKSTE erlaubte Facility-Rolle der Ziel-View
(facility_admin bzw. lead) aus Facility 1 — wenn selbst die abprallt,
prallen alle ab. Erwartet wird strikt 404: ein 403 würde die Existenz
des fremden Objekts leaken, ein 2xx wäre ein kritischer Befund.
"""

import pytest
from django.urls import reverse

from tests._authz_expectations import EXPECTATIONS

# Bekannte, als Issue dokumentierte IDOR-Lücken: (url_name, method) → Begründung.
KNOWN_IDOR_GAPS: dict[tuple[str, str], str] = {}

# Zellen ohne Leak-Potenzial: die View beantwortet die Methode pauschal
# (vor jedem Objekt-Lookup), die Antwort ist also pk-unabhängig — eine
# IDOR-Probe wäre hier bedeutungslos. (url_name, method) → Begründung.
NOT_PROBEABLE: dict[tuple[str, str], str] = {
    ("core:retention_approve", "GET"): (
        "GET antwortet hart 405 (HttpResponseNotAllowed) vor jedem Lookup — retention.py"
    ),
    ("core:retention_hold", "GET"): ("GET antwortet hart 405 (HttpResponseNotAllowed) vor jedem Lookup — retention.py"),
    ("core:retention_dismiss_hold", "GET"): (
        "GET antwortet hart 405 (HttpResponseNotAllowed) vor jedem Lookup — retention.py"
    ),
}

# POST-Bodies, ohne die die View VOR dem Objekt-Lookup ablehnt (z. B. 400
# bei fehlendem Pflichtfeld) — die Probe muss den Scoping-Code erreichen.
IDOR_POST_DATA: dict[tuple[str, str], dict[str, str]] = {
    # workitem_actions.py: Status-Validierung läuft vor get_object_or_404.
    ("core:workitem_status_update", "POST"): {"status": "done"},
}


def test_idor_dicts_reference_existing_cells():
    """Tippfehler in KNOWN_IDOR_GAPS/NOT_PROBEABLE/IDOR_POST_DATA dürfen nicht ins Leere zeigen."""
    valid = {(exp.url_name, method) for exp in EXPECTATIONS if exp.idor for method, _ in exp.methods}
    stray = (set(KNOWN_IDOR_GAPS) | set(NOT_PROBEABLE) | set(IDOR_POST_DATA)) - valid
    assert not stray, f"Einträge ohne IDOR-Zelle: {sorted(stray)}"


def _idor_cells():
    """Generiert je IDOR-deklariertem Endpoint × Methode einen Test-Parameter."""
    for exp in EXPECTATIONS:
        if not exp.idor:
            continue
        for method, allowed in exp.methods:
            if (exp.url_name, method) in NOT_PROBEABLE:
                continue
            actor = "facility_admin" if "facility_admin" in allowed else "lead"
            assert actor in allowed, f"{exp.url_name}: keine Facility-Rolle erlaubt?"
            marks = []
            if (exp.url_name, method) in KNOWN_IDOR_GAPS:
                marks.append(pytest.mark.xfail(reason=KNOWN_IDOR_GAPS[(exp.url_name, method)], strict=True))
            yield pytest.param(exp, method, actor, id=f"{exp.url_name}-{method}", marks=marks)


def _resolve_foreign_kwargs(exp, request):
    """Löst die idor-Kwargs der Tabelle auf (durchgängig Fixture-Attributpfade)."""
    kwargs = {}
    for name, ref in exp.idor:
        fixture_name, attr = ref.split(".", 1)
        kwargs[name] = getattr(request.getfixturevalue(fixture_name), attr)
    return kwargs


@pytest.fixture
def idor_actor_users(db, facility):
    """Die zwei stärksten Facility-Rollen aus Facility 1 (analog matrix_users)."""
    from core.models import User

    def make(username, role):
        return User.objects.create_user(
            username=username,
            role=role,
            facility=facility,
            is_staff=True,
            can_confirm_deletion=True,
        )

    return {
        "facility_admin": make("idor_admin", User.Role.FACILITY_ADMIN),
        "lead": make("idor_lead", User.Role.LEAD),
    }


@pytest.mark.django_db
@pytest.mark.parametrize("exp,method,actor", _idor_cells())
def test_idor_foreign_object_is_404(client, exp, method, actor, idor_actor_users, request):
    """Eine IDOR-Zelle: Zugriff auf das Objekt der zweiten Facility muss 404 liefern."""
    kwargs = _resolve_foreign_kwargs(exp, request)
    url = reverse(exp.url_name, kwargs=kwargs)
    client.force_login(idor_actor_users[actor])

    data = IDOR_POST_DATA.get((exp.url_name, method))
    response = getattr(client, method.lower())(url, data)

    assert response.status_code == 404, (
        f"{url}: fremdes Objekt antwortete {response.status_code} statt 404 "
        f"({'Existenz-Leak' if response.status_code == 403 else 'MANDANTENTRENNUNG VERLETZT'})"
    )
