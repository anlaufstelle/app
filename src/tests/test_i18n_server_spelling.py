"""Public server output preserves German spelling (Refs #2591)."""

from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.messages import get_messages
from django.core.management import CommandError, call_command, load_command_class
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils.translation import override

from core.middleware.maintenance import MaintenanceModeMiddleware
from core.models import Case, Client, User
from core.services.compliance.vvt import get_activity
from core.services.file_vault.quota import StorageUsage, storage_warning


@pytest.mark.parametrize(
    "expected",
    [
        "Legt einen Super-Admin (installation-weite Systemadministration, Refs #867) "
        "interaktiv an. Passwort wird über getpass abgefragt — kein Default.",
        "Bestätigung überspringen, wenn bereits ein Super-Admin existiert. Ohne --force bricht der Befehl ab.",
    ],
    ids=["description", "force"],
)
def test_bootstrap_help_spelling(expected):
    command = load_command_class("core", "create_super_admin")
    with override("de"):
        parser = command.create_parser("manage.py", "create_super_admin")
        help_text = " ".join(parser.format_help().split())
    assert expected in help_text


@pytest.mark.django_db
@pytest.mark.parametrize("output", ["username", "confirmation", "mismatch"])
def test_interactive_bootstrap_spelling(output):
    User.objects.create_user(username="existing", role=User.Role.STAFF)
    stderr = StringIO()
    with (
        override("de"),
        patch("builtins.input", side_effect=["existing", "new-admin"]),
        patch(
            "getpass.getpass", side_effect=["StrongPass!2026", "OtherPass!2026", "StrongPass!2026", "StrongPass!2026"]
        ) as password,
    ):
        call_command("create_super_admin", email="admin@example.org", stderr=stderr, stdout=StringIO())
    if output == "username":
        assert (
            stderr.getvalue().splitlines()[0] == "Benutzer 'existing' existiert bereits — bitte anderen Namen wählen."
        )
    elif output == "confirmation":
        assert password.call_args_list[1].args == ("Passwort (Bestätigung): ",)
    else:
        assert stderr.getvalue().splitlines()[1] == "Passwörter stimmen nicht überein."


@pytest.mark.django_db
def test_unlock_error_spelling():
    with override("de"), pytest.raises(CommandError) as error:
        call_command("unlock", "missing-user")
    assert str(error.value) == "Benutzer 'missing-user' existiert nicht. Bitte Username prüfen."


@pytest.mark.parametrize(
    ("activity_id", "field", "index", "expected"),
    [
        (
            "klienten_stammdaten",
            "purpose",
            None,
            "Beratungsdokumentation und Falllaufzeit-Steuerung in "
            "niedrigschwelligen Einrichtungen. Kein Klarname-Feld — der primäre Identifikator ist ein Pseudonym.",
        ),
        ("auditlog", "data_categories", 5, "Zugehörige Einrichtung (oder NULL bei System-Events)"),
        ("backup", "toms", 3, "Regelmäßige Restore-Tests"),
        ("dsgvo_requests", "title", None, "DSGVO-Auskunfts- und Berichtigungsanträge"),
    ],
    ids=["identifier", "facility", "restore", "requests"],
)
def test_processing_activity_spelling(activity_id, field, index, expected):
    with override("de"):
        value = get_activity(activity_id)[field]
        assert str(value if index is None else value[index]) == expected


@pytest.mark.django_db
@pytest.mark.parametrize("workflow", ["case", "client", "merge"])
def test_missing_reason_message_spelling(workflow, client, staff_user, facility, client_qualified):
    client.force_login(staff_user)
    if workflow == "case":
        case = Case.objects.create(facility=facility, client=client_qualified, title="Test case", created_by=staff_user)
        url = reverse("core:case_delete_request", args=[case.pk])
        data = {"reason": " "}
    elif workflow == "client":
        url = reverse("core:client_delete_request", args=[client_qualified.pk])
        data = {"reason": " "}
    else:
        target = Client.objects.create(facility=facility, pseudonym="Target", created_by=staff_user)
        url = reverse("core:client_merge", args=[client_qualified.pk])
        data = {"target": str(target.pk), "reason": " "}
    with override("de"):
        response = client.post(url, data)
        assert response.status_code == 302
        assert [str(message) for message in get_messages(response.wsgi_request)] == ["Bitte eine Begründung angeben."]


def test_storage_warning_spelling():
    mb = 1024 * 1024
    usage = StorageUsage(used_bytes=90 * mb, trash_bytes=10 * mb, limit_bytes=100 * mb)
    with override("de"):
        assert str(storage_warning(usage)) == (
            "Speicherkontingent zu 90 % belegt (90 von 100 MB, davon 10 MB im Papierkorb) "
            "— Papierkorb leeren oder Kontingent erhöhen."
        )


def test_maintenance_message_spelling(tmp_path):
    flag = tmp_path / "maintenance.flag"
    flag.touch()
    MaintenanceModeMiddleware._cache = None
    try:
        with override("de"), override_settings(MAINTENANCE_FLAG_FILE=str(flag)):
            response = MaintenanceModeMiddleware(lambda request: request)(RequestFactory().get("/clients/"))
        assert response.status_code == 503
        assert (
            "Anlaufstelle ist gerade kurz nicht erreichbar — wir spielen ein Update ein "
            "oder führen eine Migration durch."
        ) in response.content.decode()
    finally:
        MaintenanceModeMiddleware._cache = None
