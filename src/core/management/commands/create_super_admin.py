"""Production-Bootstrap-Command: anlegen eines Super-Admin-Users.

Refs #867: Persona Jonas (Systemadministration) — installation-weiter
Admin ohne Facility-Bindung. Interaktiv, kein hardcoded Default-Passwort.
"""

import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext_lazy as _

from core.models import User


class Command(BaseCommand):
    help = str(
        _(
            "Legt einen Super-Admin (installation-weite Systemadministration, Refs #867) "
            "interaktiv an. Passwort wird ueber getpass abgefragt — kein Default."
        )
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            dest="username",
            default=None,
            help=str(_("Benutzername. Wird interaktiv abgefragt, falls nicht angegeben.")),
        )
        parser.add_argument(
            "--email",
            dest="email",
            default=None,
            help=str(_("E-Mail-Adresse. Wird interaktiv abgefragt, falls nicht angegeben.")),
        )
        parser.add_argument(
            "--force",
            dest="force",
            action="store_true",
            default=False,
            help=str(
                _(
                    "Bestaetigung ueberspringen, wenn bereits ein Super-Admin existiert. "
                    "Ohne --force bricht der Befehl ab."
                )
            ),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _prompt(self, label: str) -> str:
        """Read a non-empty line from stdin (input()-Wrapper fuer Tests)."""
        return input(label).strip()

    def _prompt_username(self) -> str:
        while True:
            username = self._prompt(str(_("Benutzername: ")))
            if not username:
                self.stderr.write(self.style.ERROR(str(_("Benutzername darf nicht leer sein."))))
                continue
            if User.objects.filter(username=username).exists():
                msg = str(_("Benutzer '%s' existiert bereits — bitte anderen Namen waehlen.")) % username
                self.stderr.write(self.style.ERROR(msg))
                continue
            return username

    def _prompt_email(self) -> str:
        while True:
            email = self._prompt(str(_("E-Mail-Adresse: ")))
            if email:
                return email
            self.stderr.write(self.style.ERROR(str(_("E-Mail-Adresse darf nicht leer sein."))))

    def _prompt_password(self, user: User) -> str:
        """Read password twice via getpass und validiere mit Djangos Pruefkette."""
        while True:
            password = getpass.getpass(str(_("Passwort: ")))
            if not password:
                self.stderr.write(self.style.ERROR(str(_("Passwort darf nicht leer sein."))))
                continue
            confirm = getpass.getpass(str(_("Passwort (Bestaetigung): ")))
            if password != confirm:
                self.stderr.write(self.style.ERROR(str(_("Passwoerter stimmen nicht ueberein."))))
                continue
            try:
                validate_password(password, user=user)
            except ValidationError as exc:
                for message in exc.messages:
                    self.stderr.write(self.style.ERROR(message))
                continue
            return password

    # ------------------------------------------------------------------
    # Command entry
    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        force: bool = options["force"]

        # Idempotenz: bereits vorhandener super_admin?
        existing = User.objects.filter(role=User.Role.SUPER_ADMIN).first()
        if existing is not None and not force:
            raise CommandError(
                str(
                    _(
                        "Es existiert bereits ein Super-Admin ('%s'). Mit --force kannst du trotzdem "
                        "einen weiteren anlegen."
                    )
                )
                % existing.username
            )

        # Username
        username = options.get("username")
        if username:
            if User.objects.filter(username=username).exists():
                raise CommandError(str(_("Benutzer '%s' existiert bereits.")) % username)
        else:
            username = self._prompt_username()

        # Email
        email = options.get("email") or self._prompt_email()

        # Passwort (mit Validierung gegen einen vorlaeufigen User-Stub).
        stub = User(username=username, email=email, role=User.Role.SUPER_ADMIN)
        password = self._prompt_password(stub)

        # Anlage. ``create_user`` setzt das Passwort gehasht; ``role`` muss
        # als kwarg uebergeben werden (Custom-Field auf AbstractUser).
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            role=User.Role.SUPER_ADMIN,
        )
        user.facility = None
        user.is_active = True
        user.must_change_password = False
        user.save(update_fields=["facility", "is_active", "must_change_password"])

        self.stdout.write(
            self.style.SUCCESS(
                str(_("Super-Admin angelegt: %s — Login unter /login/, Bereich /system/.")) % user.username
            )
        )
