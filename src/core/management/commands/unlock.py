"""CLI-Tool: Account-Sperre eines Users aufheben.

Refs #867: schreibt einen LOGIN_UNLOCK-AuditLog, sodass nachfolgende
``is_locked``-Pruefungen Fehlversuche vor dem Unlock ignorieren.
``unlocked_by=None`` markiert den CLI-Kontext (vs. Django-Admin-Action).
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext_lazy as _

from core.models import User
from core.services import login_lockout


class Command(BaseCommand):
    help = str(_("Hebt die Account-Sperre eines Users auf (CLI-Kontext, schreibt AuditLog)."))

    def add_arguments(self, parser):
        parser.add_argument(
            "username",
            help=str(_("Benutzername des zu entsperrenden Accounts.")),
        )

    def handle(self, *args, **options):
        username = options["username"]
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist as exc:
            raise CommandError(str(_("Benutzer '%s' existiert nicht. Bitte Username pruefen.")) % username) from exc

        login_lockout.unlock(user, unlocked_by=None, ip_address=None)
        self.stdout.write(self.style.SUCCESS(str(_("Account-Sperre aufgehoben: %s")) % user.username))
