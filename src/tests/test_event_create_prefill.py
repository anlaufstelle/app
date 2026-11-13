"""Case prefill in ``EventCreateView`` (Refs #1801, section 6.5; #1813 T4).

``?case=`` also prefills the case's person unless an explicit ``?client=``
takes precedence. ``get_case_or_none`` fails closed for foreign or revoked
cases to prevent an existence oracle, matching ``resolve_writable_client``
for person prefill.

The person and case fields use Alpine/JS (``eventClientAutocomplete``,
``static/js/alpine/forms.js``), rather than Django widget ``initial`` values.
The server supplies ``data-initial-*`` attributes on the form for ``init()``
and ``loadCasesForClient()`` to consume.
"""

import pytest
from django.urls import reverse

from core.models import Case


@pytest.mark.django_db
class TestEventCreatePrefill:
    def test_case_param_preselects_case(self, client, staff_user, facility, case_open):
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"), {"case": str(case_open.pk)})
        assert response.status_code == 200
        content = response.content.decode()
        assert f'data-initial-case-id="{case_open.pk}"' in content

    def test_case_param_derives_client_from_case(self, client, staff_user, facility, case_open, client_identified):
        assert case_open.client_id == client_identified.pk
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"), {"case": str(case_open.pk)})
        assert response.status_code == 200
        content = response.content.decode()
        assert f'data-initial-client-id="{client_identified.pk}"' in content
        assert f'data-initial-pseudonym="{client_identified.pseudonym}"' in content

    def test_foreign_case_param_prefills_nothing(self, client, staff_user, facility, other_facility, client_identified):
        foreign_case = Case.objects.create(
            facility=other_facility,
            client=client_identified,
            title="Fremder Fall",
            status=Case.Status.OPEN,
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:event_create"), {"case": str(foreign_case.pk)})
        assert response.status_code == 200
        content = response.content.decode()
        # Fail-closed: kein abgeleiteter Personenbezug über einen fremden Fall
        # hinweg — das wäre ein Existenz-Orakel (get_case_or_none scoped).
        assert 'data-initial-client-id=""' in content
        assert 'data-initial-pseudonym=""' in content

    def test_explicit_client_param_wins_over_case_derived_client(
        self, client, staff_user, facility, case_open, client_identified, client_qualified
    ):
        """``case_open`` gehört zu ``client_identified`` — ein expliziter
        ``?client=<anderer>`` überschreibt die Ableitung aus dem Fall."""
        assert case_open.client_id != client_qualified.pk
        client.force_login(staff_user)
        response = client.get(
            reverse("core:event_create"), {"case": str(case_open.pk), "client": str(client_qualified.pk)}
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert f'data-initial-client-id="{client_qualified.pk}"' in content
        assert f'data-initial-pseudonym="{client_qualified.pseudonym}"' in content
