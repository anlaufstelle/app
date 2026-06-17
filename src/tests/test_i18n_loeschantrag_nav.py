"""Refs #1144: i18n-Korrekturen.

1. Der Erfolgs-Flash beim Personen-Löschantrag muss den Umlaut „Löschantrag"
   tragen (Quelle war ASCII „Loeschantrag" — bei leerem DE-msgstr landete der
   ASCII-String direkt in der UI).
2. Englische Nav-Labels: „Dateien" → „Files" (war „Data"), „Löschfristen" →
   „Deletion deadlines" (war „Delete").
"""

import pytest
from django.urls import reverse
from django.utils.translation import gettext, override


@pytest.mark.django_db
class TestDeletionRequestFlashUmlaut:
    def test_flash_uses_umlaut(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:client_delete_request", kwargs={"pk": client_identified.pk}),
            {"reason": "Test-Begründung"},
            follow=True,
        )
        content = response.content.decode()
        assert "Löschantrag gestellt" in content
        assert "Loeschantrag gestellt" not in content


class TestEnglishNavLabels:
    def test_dateien_translates_to_files(self):
        with override("en"):
            assert gettext("Dateien") == "Files"

    def test_loeschfristen_translates_to_deletion_deadlines(self):
        with override("en"):
            assert gettext("Löschfristen") == "Deletion deadlines"
