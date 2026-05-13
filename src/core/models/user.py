"""Custom User model with 5 roles."""

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    """Custom User for Anlaufstelle with role and facility assignment."""

    class Role(models.TextChoices):
        # Refs #867: super_admin operiert installation-weit (Persona Jonas);
        # facility_admin (frueher 'admin') ist auf eine Einrichtung beschraenkt.
        SUPER_ADMIN = "super_admin", _("Systemadministration")
        FACILITY_ADMIN = "facility_admin", _("Anwendungsbetreuung")
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
    mfa_required = models.BooleanField(
        default=False,
        verbose_name=_("2FA erforderlich"),
        help_text=_(
            "Wenn aktiv, muss der Nutzer ein TOTP-Gerät einrichten, bevor er die "
            "Anwendung weiter nutzen kann. Wird automatisch gesetzt, wenn die "
            "Einrichtung 2FA für alle verlangt."
        ),
    )
    preferred_language = models.CharField(
        max_length=5,
        choices=settings.LANGUAGES,
        default=settings.LANGUAGE_CODE,
        verbose_name=_("Bevorzugte Sprache"),
    )
    offline_key_salt = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name=_("Offline-Schlüssel-Salt"),
        help_text=_(
            "Base64URL-Salt für die client-seitige PBKDF2-Ableitung. "
            "Lazy generiert beim ersten Salt-Endpoint-Aufruf, "
            "rotiert bei Passwort-Änderung."
        ),
    )

    class Meta:
        verbose_name = _("Benutzer")
        verbose_name_plural = _("Benutzer")

    def __str__(self):
        return self.display_name or self.get_full_name() or self.username

    @property
    def is_super_admin(self):
        return self.role == self.Role.SUPER_ADMIN

    @property
    def is_facility_admin(self):
        return self.role == self.Role.FACILITY_ADMIN

    @property
    def is_lead_or_admin(self):
        return self.role in (self.Role.FACILITY_ADMIN, self.Role.LEAD)

    @property
    def is_staff_or_above(self):
        return self.role in (self.Role.FACILITY_ADMIN, self.Role.LEAD, self.Role.STAFF)

    @property
    def is_assistant_or_above(self):
        return self.role in (self.Role.FACILITY_ADMIN, self.Role.LEAD, self.Role.STAFF, self.Role.ASSISTANT)

    @property
    def has_confirmed_totp_device(self):
        """True when the user has at least one confirmed TOTP device."""
        from django_otp.plugins.otp_totp.models import TOTPDevice

        return TOTPDevice.objects.filter(user=self, confirmed=True).exists()

    @property
    def is_mfa_enforced(self):
        """True if either the user or the user's facility requires 2FA."""
        if self.mfa_required:
            return True
        facility = self.facility
        if facility is None:
            return False
        try:
            return bool(facility.settings.mfa_enforced_facility_wide)
        except facility._meta.model.settings.RelatedObjectDoesNotExist:
            return False
