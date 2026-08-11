"""Custom User model with 5 roles."""

import base64
import secrets

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.functional import cached_property
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
    mfa_setup_deadline = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Frist für 2FA-Einrichtung"),
        help_text=_(
            "Ende der Übergangsfrist, bis zu der ein rollen-erzwungener Nutzer "
            "die Zwei-Faktor-Authentifizierung einrichten muss. Wird beim ersten "
            "Login auf 30 Tage gesetzt; nach Ablauf ist der Zugang bis zur "
            "Einrichtung gesperrt (Refs #689)."
        ),
    )
    preferred_language = models.CharField(
        max_length=5,
        choices=settings.LANGUAGES,
        default=settings.LANGUAGE_CODE,
        verbose_name=_("Bevorzugte Sprache"),
    )
    can_confirm_deletion = models.BooleanField(
        default=False,
        verbose_name=_("Löschbestätigung"),
        help_text=_(
            "Darf Löschanträge als zweite Person genehmigen (Vier-Augen-"
            "Prinzip). Rollenunabhängig vergebbar, damit auch bei einer "
            "einzelnen Leitung ein Genehmiger verfügbar ist. Eigene "
            "Anträge bleiben ausgeschlossen."
        ),
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
    def sees_arbeitszentrale(self):
        """Nav-Gate für ``/start/`` (Regel N3, Refs #1813, Konzept #1801).

        Nach der Vervollständigung der Rollen-Einstiege landet nur noch die
        Leitung auf einer eigenen Arbeitszentrale-Seite: Fachkraft/Assistenz →
        Zeitstrom, Anwendungsbetreuung → ``/verwaltung/``, Systemadministration
        → ``/system/``. Ein Nav-Eintrag für eine Rolle, die dort nur einen
        Redirect bekommt, ist ein Umweg mit Etikett.

        Lebt wie ``can_access_deletion_requests`` am Modell, damit Navigation
        und View garantiert dieselbe Bedingung auswerten.
        """
        return self.role == self.Role.LEAD

    @cached_property
    def has_open_deletion_requests(self):
        """True, wenn diese Person einen noch offenen Löschantrag gestellt hat.

        Refs #1736 (B): Rückkanal für die Antragstellerin. Ohne ihn verschwand
        ein gestellter Antrag für sie spurlos — die Löschanträge-Liste war auf
        Leitung/Admin bzw. Genehmiger:innen gegated.

        ``cached_property`` (Refs #1755, Perf): Beide Navigationsleisten
        (_navigation_desktop/_mobile) werten das Gate auf JEDER Seite aus — für
        Fachkraft/Assistenz (ohne is_lead_or_admin/can_confirm_deletion) lief so
        pro Request ein ungecachtes ``EXISTS`` **doppelt**. Die Nav teilt sich
        eine Request-``User``-Instanz, der Cache reduziert das auf eine Query.
        Ein Löschantrag wird per POST-Redirect-GET gestellt (``ClientDelete
        RequestView`` → ``redirect`` auf die Detailseite) — die Folge-Seite lädt
        eine frische Instanz, es gibt also kein Stale-Read nach dem Schreiben.
        """
        from core.models.workitem import DeletionRequest

        if self.facility_id is None:
            return False
        return DeletionRequest.objects.filter(
            facility_id=self.facility_id,
            requested_by=self,
            status=DeletionRequest.Status.PENDING,
        ).exists()

    @property
    def can_access_deletion_requests(self):
        """Gate für die Löschanträge-Liste — Navigation UND View teilen es.

        Refs #1053 (Genehmiger-Pool), #1736 (B): Vorher gateten Navigation
        (``is_lead_or_admin``) und View (``DeletionRequestListAccessMixin``)
        unterschiedlich; Träger:innen des Rechts „Löschbestätigung" ohne
        Leitungsrolle fanden den Einstieg nur per Direkt-URL. Die
        Reihenfolge der Prüfungen ist bewusst: die beiden Feld-/Rollen-
        Bedingungen kosten keine Query, der Antragsteller-Zweig eine.
        """
        if self.is_lead_or_admin or self.can_confirm_deletion:
            return True
        return self.has_open_deletion_requests

    @property
    def sees_all_deletion_requests(self):
        """True, wenn die Löschanträge-Liste ALLE Anträge zeigen darf.

        Refs #1736 (B): Wer nur wegen eines eigenen offenen Antrags Zugang
        hat, sieht ausschließlich die eigenen Anträge — die Liste ist sonst
        eine Fremd-PII-Fläche (Begründungen, Ziel-Pseudonyme).
        """
        return self.is_lead_or_admin or self.can_confirm_deletion

    @property
    def has_confirmed_totp_device(self):
        """True when the user has at least one confirmed TOTP device."""
        # App-weit laeuft der TOTP-Zugriff ueber das Proxy-Modell mit at-rest
        # verschluesseltem Secret (Refs #1362); ``exists()`` liest kein Secret,
        # die Wahl ist reine Konsistenz.
        from core.models.mfa import EncryptedTOTPDevice

        return EncryptedTOTPDevice.objects.filter(user=self, confirmed=True).exists()

    @property
    def has_confirmed_webauthn_device(self):
        """True when the user has at least one confirmed WebAuthn credential (Passkey).

        Passkeys sind django-otp-Devices (ADR-032, Refs #1492); ``confirmed=True``
        markiert ein einsatzbereites Credential (bei der Registrierung ueber die
        bereits verifizierte Session sofort gesetzt).
        """
        from django_otp_webauthn.models import WebAuthnCredential

        return WebAuthnCredential.objects.filter(user=self, confirmed=True).exists()

    @property
    def has_confirmed_mfa_device(self):
        """True when the user has ANY confirmed second factor (TOTP oder Passkey).

        Methoden-uebergreifendes Praedikat (ADR-032, Refs #1492): loest das
        TOTP-spezifische ``has_confirmed_totp_device`` in MFA-Enforcement und
        Verify-Flow ab, sodass ein Passkey als vollwertiger zweiter Faktor zaehlt.
        Passkeys werden nur ZUSAETZLICH zu einem TOTP-Geraet vergeben (der
        Backup-Code-Recovery-Anker haengt an der TOTP-Einrichtung), daher
        impliziert ein Passkey in der Praxis stets auch ein TOTP-Geraet.
        """
        return self.has_confirmed_totp_device or self.has_confirmed_webauthn_device

    @property
    def mfa_enforced_deliberately(self):
        """MFA-Zwang aus einer bewussten Einzel-/Einrichtungsentscheidung.

        ``mfa_required`` (per Admin-Aktion/Invite/create_super_admin gesetzt) und
        das facility-weite Flag ``mfa_enforced_facility_wide`` greifen OHNE
        Grace-Frist — der Zwang wurde bewusst und gezielt aktiviert, ein
        sofortiger Setup-Redirect ist gewollt (Refs #689).
        """
        if self.mfa_required:
            return True
        facility = self.facility
        if facility is None:
            return False
        try:
            return bool(facility.settings.mfa_enforced_facility_wide)
        except facility._meta.model.settings.RelatedObjectDoesNotExist:
            return False

    @property
    def mfa_enforced_by_role_immediate(self):
        """SOFORT greifender Rollen-Zwang (Refs #1019).

        Die in ``settings.MFA_ENFORCED_ROLES_IMMEDIATE`` gefuehrten Rollen
        (Default super_admin/facility_admin) erzwingen MFA OHNE Grace-Frist — ein
        solcher Account ohne zweiten Faktor wird von der
        ``MFAEnforcementMiddleware`` sofort auf ``/mfa/setup/`` geleitet.
        Greift nur bei aktivem ``MFA_ENFORCE_PRIVILEGED_ROLES`` (nur Produktion —
        dev/test/e2e/demo bleiben bewusst MFA-frei).
        """
        return settings.MFA_ENFORCE_PRIVILEGED_ROLES and self.role in settings.MFA_ENFORCED_ROLES_IMMEDIATE

    @property
    def mfa_enforced_by_role_grace(self):
        """Rollen-Zwang MIT 30-Tage-Grace-Frist (Refs #689/#1639).

        Die in ``settings.MFA_ENFORCED_ROLES_GRACE`` gefuehrten Rollen (Default
        lead/staff/assistant) erzwingen MFA ueber ihre Rolle, aber erst nach
        Ablauf der Setup-Frist (Durchsetzung in ``MFAEnforcementMiddleware``),
        damit Bestandsnutzer nicht ausgesperrt werden. Greift nur bei aktivem
        ``MFA_ENFORCE_PRIVILEGED_ROLES``.
        """
        return settings.MFA_ENFORCE_PRIVILEGED_ROLES and self.role in settings.MFA_ENFORCED_ROLES_GRACE

    @property
    def mfa_enforced_by_role(self):
        """Rollen-Default-Enforcement gesamt (sofort ODER grace-behaftet).

        Vereint ``mfa_enforced_by_role_immediate`` (A3.1, Refs #1019) und
        ``mfa_enforced_by_role_grace`` (Refs #689) fuer Konsumenten, die nur
        wissen muessen OB die Rolle MFA erzwingt (z.B. ``is_mfa_enforced``).
        WANN der Zwang greift — sofort vs. nach Grace-Frist — entscheidet die
        ``MFAEnforcementMiddleware`` ueber die beiden Teil-Properties.
        """
        return self.mfa_enforced_by_role_immediate or self.mfa_enforced_by_role_grace

    @property
    def is_mfa_enforced(self):
        """True if the user ultimately requires 2FA (deliberate OR role-based).

        Contract unveraendert (genutzt u.a. in ``MFASettingsView`` und
        ``MFADisableView``): sagt, ob 2FA fuer diesen Nutzer verpflichtend IST.
        WANN der Zwang greift — sofort (bewusst) vs. nach Grace-Frist (Rolle) —
        entscheidet die Middleware ueber die beiden Teil-Properties.
        """
        return self.mfa_enforced_deliberately or self.mfa_enforced_by_role

    def ensure_offline_key_salt(self) -> str:
        """Return the user's offline salt, lazily generating one on first access.

        Base64URL-Salt (16 Byte Entropie aus ``secrets.token_bytes``, padding-frei)
        fuer die client-seitige PBKDF2-Ableitung des AES-GCM-256-Session-Keys.
        Der Salt wird nicht rotiert ausser bei Passwort-Aenderung (gesetzt in
        ``CustomPasswordChangeView.form_valid``), damit ein aktiver Session-Tab
        nach Reload denselben Key re-derivieren kann.

        Refs #958 — ersetzt den frueheren ``services/offline_keys``-Service.
        """
        if not self.offline_key_salt:
            salt_bytes = secrets.token_bytes(16)
            self.offline_key_salt = base64.urlsafe_b64encode(salt_bytes).decode().rstrip("=")
            self.save(update_fields=["offline_key_salt"])
        return self.offline_key_salt


def mfa_enforced_roles():
    """Gesamtmenge aller rollen-erzwungenen MFA-Rollen (sofort + grace).

    EINE gemeinsame Quelle fuer Konsumenten, die nur die Rollenmenge brauchen
    (z.B. die Compliance-Quote ``compliance.mfa._mfa_checks``, Refs #1478/COD-12).
    Die beiden Teilmengen sind per Setting konfigurierbar (Refs #1639):
    ``MFA_ENFORCED_ROLES_IMMEDIATE`` (sofort, Refs #1019) und
    ``MFA_ENFORCED_ROLES_GRACE`` (nach der 30-Tage-Grace-Frist, Refs #689).
    OB sofort oder erst nach der Frist erzwungen wird, entscheidet die
    ``MFAEnforcementMiddleware`` ueber die beiden Teil-Properties.
    """
    return (*settings.MFA_ENFORCED_ROLES_IMMEDIATE, *settings.MFA_ENFORCED_ROLES_GRACE)
