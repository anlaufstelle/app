"""Navigation labels disclose their limits through the public editor routes."""

import pytest
from django.urls import reverse
from django.utils.translation import override

from core.models import WorkItem

pytestmark = pytest.mark.django_db

GUIDANCE = (
    "Kurz halten: keine Klarnamen und keine Angaben nach Art. 9 DSGVO. Dieses Feld wird unverschlüsselt gespeichert."
)


@pytest.mark.parametrize("kind", ["client", "case", "episode", "workitem"])
@pytest.mark.parametrize("operation", ["create", "update"])
def test_editor_displays_plaintext_label_guidance(
    client, staff_user, client_identified, case_open, episode, kind, operation
):
    client.force_login(staff_user)
    kwargs = {}
    if kind == "episode":
        kwargs["case_pk"] = case_open.pk
    if operation == "update":
        records = {"client": client_identified, "case": case_open, "episode": episode}
        if kind == "workitem":
            records[kind] = WorkItem.objects.create(
                facility=staff_user.facility, created_by=staff_user, title="Follow up"
            )
        kwargs["pk"] = records[kind].pk
    with override("de"):
        response = client.get(reverse(f"core:{kind}_{operation}", kwargs=kwargs))
        assert response.status_code == 200
        field_name = "pseudonym" if kind == "client" else "title"
        assert response.context["form"][field_name].help_text == GUIDANCE
        assert GUIDANCE in response.content.decode()
