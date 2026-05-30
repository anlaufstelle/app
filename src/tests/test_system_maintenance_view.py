"""Coverage-Tests fuer ``core.views.system.maintenance.SystemMaintenanceView``.

Deckt die Branches:

* GET ohne aktive Flag-Datei -> ``configured=True``, ``is_active=False``.
* GET mit aktiver Flag-Datei -> ``is_active=True`` + Notiz aus File.
* POST ohne konfigurierte ``MAINTENANCE_FLAG_FILE`` -> Fehler-Message + Redirect.
* POST ``enable`` schreibt die Flag-Datei (mit Notiz).
* POST ``enable`` OSError-Branch (Lines 60-63) -> Fehler-Message.
* POST ``disable`` entfernt die Flag-Datei.
* POST mit unbekannter Action (Lines 93-94) -> Fehler-Message.

Refs #922 (Welle 10 / Bucket B).
"""

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse


@pytest.mark.django_db
class TestSystemMaintenanceView:
    def _messages_for(self, response):
        """Hole Messages aus dem ``response.wsgi_request`` (Post-Redirect)."""
        return [m.message for m in get_messages(response.wsgi_request)]

    def test_get_renders_status_when_flag_configured(self, client, super_admin_user, settings, tmp_path):
        """GET: Flag-Datei nicht vorhanden -> configured=True, is_active=False."""
        settings.MAINTENANCE_FLAG_FILE = str(tmp_path / "maint.flag")
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_maintenance"))
        assert response.status_code == 200
        assert response.context["configured"] is True
        assert response.context["is_active"] is False

    def test_get_renders_active_with_note(self, client, super_admin_user, settings, tmp_path):
        """GET: Flag-Datei existiert -> is_active=True + Notiz aus Inhalt."""
        flag = tmp_path / "maint.flag"
        flag.write_text("Wartung wegen Update")
        settings.MAINTENANCE_FLAG_FILE = str(flag)
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_maintenance"))
        assert response.status_code == 200
        assert response.context["is_active"] is True
        assert response.context["note"] == "Wartung wegen Update"

    def test_post_without_flag_setting_shows_error(self, client, super_admin_user, settings):
        """Lines 42-50: ``MAINTENANCE_FLAG_FILE = None`` -> Error + Redirect."""
        settings.MAINTENANCE_FLAG_FILE = None
        client.force_login(super_admin_user)
        response = client.post(reverse("core:system_maintenance"), {"action": "enable"})
        assert response.status_code == 302
        msgs = self._messages_for(response)
        assert any("nicht konfiguriert" in m for m in msgs)

    def test_post_enable_writes_flag(self, client, super_admin_user, settings, tmp_path):
        """Happy-Path enable: Flag-Datei wird mit Notiz erstellt."""
        flag = tmp_path / "m.flag"
        settings.MAINTENANCE_FLAG_FILE = str(flag)
        client.force_login(super_admin_user)
        response = client.post(reverse("core:system_maintenance"), {"action": "enable", "note": "test"})
        assert response.status_code == 302
        assert flag.exists()
        assert flag.read_text() == "test"

    def test_post_enable_oserror_returns_error(self, client, super_admin_user, settings):
        """Lines 60-63: ``open(...)`` wirft OSError -> Fehler-Message + Redirect."""
        # Verzeichnis existiert nicht -> open(..., "w") wirft FileNotFoundError (OSError)
        settings.MAINTENANCE_FLAG_FILE = "/no/such/dir/flag.txt"
        client.force_login(super_admin_user)
        response = client.post(reverse("core:system_maintenance"), {"action": "enable"})
        assert response.status_code == 302
        msgs = self._messages_for(response)
        assert any("konnte nicht aktiviert" in m for m in msgs)

    def test_post_disable_removes_flag(self, client, super_admin_user, settings, tmp_path):
        """Happy-Path disable: Flag-Datei wird entfernt."""
        flag = tmp_path / "m.flag"
        flag.write_text("note")
        settings.MAINTENANCE_FLAG_FILE = str(flag)
        client.force_login(super_admin_user)
        response = client.post(reverse("core:system_maintenance"), {"action": "disable"})
        assert response.status_code == 302
        assert not flag.exists()

    def test_post_unknown_action_shows_error(self, client, super_admin_user, settings, tmp_path):
        """Lines 93-94: unbekannte Action -> Fehler-Message + Redirect."""
        settings.MAINTENANCE_FLAG_FILE = str(tmp_path / "m.flag")
        client.force_login(super_admin_user)
        response = client.post(reverse("core:system_maintenance"), {"action": "frobnicate"})
        assert response.status_code == 302
        msgs = self._messages_for(response)
        assert any("Unbekannte Aktion" in m for m in msgs)

    def test_post_disable_oserror_returns_error(self, client, super_admin_user, settings, tmp_path):
        """Lines 79-82: ``os.remove(...)`` wirft OSError -> Fehler-Message + Redirect."""
        from unittest.mock import patch

        flag = tmp_path / "m.flag"
        flag.write_text("note")
        settings.MAINTENANCE_FLAG_FILE = str(flag)
        client.force_login(super_admin_user)
        with patch("core.views.system.maintenance.os.remove", side_effect=OSError("permission denied")):
            response = client.post(reverse("core:system_maintenance"), {"action": "disable"})
        assert response.status_code == 302
        msgs = self._messages_for(response)
        assert any("konnte nicht deaktiviert" in m for m in msgs)

    def test_get_with_unreadable_flag_file_returns_empty_note(
        self, client, super_admin_user, settings, tmp_path
    ):
        """Lines 105-106, 110-111: ``os.stat``/``open(...)`` OSError -> activated_at=None, note=''."""
        from unittest.mock import patch

        flag = tmp_path / "m.flag"
        flag.write_text("notiz")
        settings.MAINTENANCE_FLAG_FILE = str(flag)
        client.force_login(super_admin_user)

        # Erst ``os.stat`` schlägt fehl -> activated_at bleibt None (Line 105-106).
        with patch("core.views.system.maintenance.os.stat", side_effect=OSError("stat failed")):
            response = client.get(reverse("core:system_maintenance"))
        assert response.status_code == 200
        assert response.context["activated_at"] is None

        # Zweiter Run: ``open(...)`` schlägt fehl -> note bleibt '' (Line 110-111).
        # Wir patchen `open` im Modulkontext.
        with patch("core.views.system.maintenance.open", side_effect=OSError("read failed"), create=True):
            response = client.get(reverse("core:system_maintenance"))
        assert response.status_code == 200
        assert response.context["note"] == ""
