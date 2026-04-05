"""Custom User model with 4 roles."""

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    """Custom User for Anlaufstelle with role and facility assignment."""

    class Role(models.TextChoices):
        ADMIN = "admin", _("Administrator")
        LEAD = "lead", _("Leitung")
        STAFF = "staff", _("Fachkraft")
        ASSISTANT = "assistant", _("Assistenz")

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.STAFF,
        verbose_name=_("Rolle"),
    )
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
        verbose_name=_("Einrichtung"),
    )
    display_name = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Anzeigename"),
    )
    phone = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_("Telefon"),
    )
    notes = models.TextField(
        blank=True,
        verbose_name=_("Notizen"),
    )
    must_change_password = models.BooleanField(
        default=False,
        verbose_name=_("Passwort muss geändert werden"),
    )
    preferred_language = models.CharField(
        max_length=5,
        choices=settings.LANGUAGES,
        default=settings.LANGUAGE_CODE,
        verbose_name=_("Bevorzugte Sprache"),
    )

    class Meta:
        verbose_name = _("Benutzer")
        verbose_name_plural = _("Benutzer")

    def __str__(self):
        return self.display_name or self.get_full_name() or self.username

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN

    @property
    def is_lead_or_admin(self):
        return self.role in (self.Role.ADMIN, self.Role.LEAD)

    @property
    def is_staff_or_above(self):
        return self.role in (self.Role.ADMIN, self.Role.LEAD, self.Role.STAFF)

    @property
    def is_assistant_or_above(self):
        return self.role in (self.Role.ADMIN, self.Role.LEAD, self.Role.STAFF, self.Role.ASSISTANT)
