"""Rückkanal + ehrliche Texte im Personen-Löschworkflow (Refs #1736 B/D/F).

Die antragstellende Fachkraft bekam nach dem Antrag nichts mehr zu sehen:
Der Nav-Eintrag „Löschanträge" war auf ``is_lead_or_admin`` gegated, obwohl
das View-Gate auch Träger von ``can_confirm_deletion`` zulässt (Drift). Die
Antragsseite sprach zudem von „der Leitung", tatsächlich entscheidet der
Pool ``can_confirm_deletion`` — und die Papierkorb-Semantik war nirgends
erklärt.
"""

import pytest
from django.urls import reverse

from core.models import User
from core.services.client import request_client_deletion
from core.services.events import request_deletion

pytestmark = pytest.mark.django_db

LIST_URL_NAME = "core:deletion_request_list"


@pytest.fixture
def confirmer_assistant(facility):
    user = User.objects.create_user(
        username="testconfirmer",
        role=User.Role.ASSISTANT,
        facility=facility,
        is_staff=True,
        can_confirm_deletion=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


class TestListGateMatchesNavigation:
    def test_staff_without_own_request_stays_denied(self, client, staff_user):
        client.force_login(staff_user)
        assert client.get(reverse(LIST_URL_NAME)).status_code == 403

    def test_staff_with_own_pending_request_may_open_list(self, client, staff_user, client_identified):
        request_client_deletion(client_identified, staff_user, "Widerruf")
        client.force_login(staff_user)
        assert client.get(reverse(LIST_URL_NAME)).status_code == 200

    def test_confirmer_without_lead_role_may_open_list(self, client, confirmer_assistant):
        client.force_login(confirmer_assistant)
        assert client.get(reverse(LIST_URL_NAME)).status_code == 200

    def test_requester_sees_only_own_requests(self, client, staff_user, lead_user, client_identified, sample_event):
        own = request_client_deletion(client_identified, staff_user, "Eigener Grund")
        foreign = request_deletion(sample_event, lead_user, "Fremder Grund")
        client.force_login(staff_user)
        response = client.get(reverse(LIST_URL_NAME))
        pending = list(response.context["pending_requests"])
        assert own in pending
        assert foreign not in pending

    def test_lead_still_sees_all_requests(self, client, staff_user, lead_user, client_identified, sample_event):
        own = request_client_deletion(client_identified, staff_user, "Eigener Grund")
        foreign = request_deletion(sample_event, lead_user, "Fremder Grund")
        client.force_login(lead_user)
        pending = list(client.get(reverse(LIST_URL_NAME)).context["pending_requests"])
        assert own in pending
        assert foreign in pending


class TestNavigationEntry:
    """Refs #1736: Wer die Antragsliste sehen darf, findet sie in der Navigation.

    Refs #1813 (Etappe 2/3): Der Einstieg heisst jetzt „Datenschutz" und ist
    der Bereichs-Hub — die acht Einzel-Eintraege sind seine Reiter geworden,
    auf Desktop wie mobil. Wer dort nur EINEN sichtbaren Reiter hat (die
    antragstellende Fachkraft, die Traegerin des Rechts „Loeschbestaetigung"),
    bekommt keine leere Huelle, sondern eine Weiterleitung direkt auf die
    Liste (Regel N2 + Einzelreiter-Weiterleitung, `test_navigation_hubs.py`).

    Der Test greift deshalb nicht mehr nach dem Listen-Literal im Seitenkoerper,
    sondern laeuft den Weg ab. Das ist eine geaenderte Anforderung, keine
    abgeschwaechte Assertion — und die Aussage wird staerker: vorher belegte
    ein Treffer irgendwo im HTML „Link da", jetzt steht da „Einstieg sichtbar
    UND er kommt an".
    """

    HUB_URL_NAME = "core:datenschutz_uebersicht"

    def _nav_body(self, client, user):
        client.force_login(user)
        return client.get(reverse("core:client_list")).content.decode()

    def _lands_on_the_list(self, client, user):
        client.force_login(user)
        response = client.get(reverse(self.HUB_URL_NAME))
        return response.status_code == 302 and response.url == reverse(LIST_URL_NAME)

    def test_hidden_for_staff_without_request(self, client, staff_user):
        body = self._nav_body(client, staff_user)
        assert reverse(self.HUB_URL_NAME) not in body
        assert reverse(LIST_URL_NAME) not in body

    def test_visible_for_staff_with_own_request(self, client, staff_user, client_identified):
        request_client_deletion(client_identified, staff_user, "Widerruf")
        assert reverse(self.HUB_URL_NAME) in self._nav_body(client, staff_user)
        assert self._lands_on_the_list(client, staff_user)

    def test_visible_for_confirmer_without_lead_role(self, client, confirmer_assistant):
        assert reverse(self.HUB_URL_NAME) in self._nav_body(client, confirmer_assistant)
        assert self._lands_on_the_list(client, confirmer_assistant)

    def test_visible_for_lead(self, client, lead_user):
        """Die Leitung sieht die Uebersicht selbst — die Liste ist dort ein Reiter."""
        assert reverse(self.HUB_URL_NAME) in self._nav_body(client, lead_user)

        client.force_login(lead_user)
        assert reverse(LIST_URL_NAME) in client.get(reverse(self.HUB_URL_NAME)).content.decode()


class TestDetailBannerLinksBack:
    def test_banner_links_to_list_for_requester(self, client, staff_user, client_identified):
        request_client_deletion(client_identified, staff_user, "Widerruf")
        client.force_login(staff_user)
        body = client.get(reverse("core:client_detail", args=[client_identified.pk])).content.decode()
        assert "client-deletion-pending-banner" in body
        assert reverse(LIST_URL_NAME) in body

    def test_banner_without_link_for_assistant(self, client, staff_user, assistant_user, client_identified):
        request_client_deletion(client_identified, staff_user, "Widerruf")
        client.force_login(assistant_user)
        body = client.get(reverse("core:client_detail", args=[client_identified.pk])).content.decode()
        assert "client-deletion-pending-banner" in body
        assert reverse(LIST_URL_NAME) not in body


class TestRequestPageNamesConfirmerPool:
    def test_page_lists_possible_confirmers(self, client, staff_user, lead_user, client_identified):
        client.force_login(staff_user)
        body = client.get(reverse("core:client_delete_request", args=[client_identified.pk])).content.decode()
        assert "delete-request-confirmers" in body
        assert lead_user.username in body

    def test_page_does_not_claim_the_lead_decides(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        body = client.get(reverse("core:client_delete_request", args=[client_identified.pk])).content.decode()
        assert "an die Leitung" not in body
        assert "Löschbestätigung" in body

    def test_setup_guard_when_no_eligible_confirmer(self, client, staff_user, client_identified):
        User.objects.exclude(pk=staff_user.pk).update(can_confirm_deletion=False)
        staff_user.can_confirm_deletion = True
        staff_user.save(update_fields=["can_confirm_deletion"])
        client.force_login(staff_user)
        body = client.get(reverse("core:client_delete_request", args=[client_identified.pk])).content.decode()
        assert "delete-request-no-confirmer" in body

    def test_trash_semantics_explained_on_request_page(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        body = client.get(reverse("core:client_delete_request", args=[client_identified.pk])).content.decode()
        assert "delete-request-trash-hint" in body


class TestTrashPageSemantics:
    def test_trash_explains_when_people_appear(self, client, admin_user):
        client.force_login(admin_user)
        body = client.get(reverse("core:client_trash")).content.decode()
        assert "client-trash-semantics-hint" in body
        # Interne Bezeichner gehören nicht in einen Nutzertext.
        assert "enforce_retention" not in body
        assert "client_trash_days" not in body
