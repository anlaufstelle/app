"""Custom AdminSite mit Rollen-Gate + Sudo-Mode-Pflicht (Refs #785).

Default-Django-AdminSite prueft nur ``is_staff``. Anlaufstelle erweitert:

- Rolle muss ``super_admin`` oder ``facility_admin`` sein
  (``lead``/``staff``/``assistant`` werden geblockt, auch wenn ``is_staff=True``).
- Sudo-Mode muss aktiv sein (Re-Auth-Schutz, Refs #683).

Modelle werden mit dem Singleton ``anlaufstelle_admin_site`` registriert
(``@admin.register(Model, site=anlaufstelle_admin_site)``). URL-Hookup in
``src/anlaufstelle/urls.py`` zeigt ``/admin-mgmt/`` auf diese Site.

Refs #958 — ``has_role_permission`` und ``scope_to_facility`` zentralisieren
die Rollen-/Facility-Logik, die vorher in den Mixins (``core/admin/mixins.py``)
dupliziert war. Die Mixins delegieren jetzt an diese Site-Methoden, damit es
nur eine Definition pro Regel gibt.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django_ratelimit.decorators import ratelimit
from unfold.forms import AuthenticationForm as UnfoldAuthenticationForm
from unfold.sites import UnfoldAdminSite

from core.services.security import is_in_sudo


def _admin_login_username_key(group, request):
    """Ratelimit-Key aus dem Username des Admin-Logins (Refs #2020).

    Wortgleich zu ``core.views.auth._login_username_key`` (bewusst dupliziert
    statt importiert: ``core.admin_site`` wird sehr frueh geladen und darf
    nicht am View-Modul haengen). Lowercased + gestrippt, damit 'Alice',
    ' alice ' und 'alice' nicht als drei unabhaengige Eimer zaehlen.
    """
    del group  # ungenutzt: der Eimer wird ueber den Usernamen gebildet
    return (request.POST.get("username") or "").lower().strip()


class AdminLockoutAuthenticationForm(UnfoldAuthenticationForm):
    """Login-Formular der Admin-Site mit Account-Lockout-Pruefung (Refs #2020).

    Der App-Login prueft den Lockout in ``CustomLoginView.form_valid``
    (``views/auth.py``); der Admin-Login fiel bis hierher auf Djangos
    Stock-Auth zurueck — ein nach 10 Fehlversuchen gesperrter Account kam dort
    mit korrektem Passwort weiterhin durch.

    ``confirm_login_allowed`` ist dafuer der richtige Haken statt eines
    View-Overrides: ``AuthenticationForm.clean()`` ruft ihn genau zwischen
    ``authenticate()`` und ``auth_login()`` auf — dieselbe Stelle im Ablauf wie
    der Check in ``form_valid``. Es existiert also noch keine gebundene
    Session, die widerrufen werden muesste, und die ``is_staff``-Pruefung der
    Django-/Unfold-Basisklasse bleibt ueber ``super()`` erhalten.

    Die Wirkung spiegelt ``CustomLoginView`` bewusst vollstaendig — Widerruf
    (Salt-Rotation + Session-Flush ueber die ACCOUNT_LOCKOUT-Wirkungsmatrix,
    Refs #1965) und ``LOGIN_FAILED``-Zeile mit ``reason="locked"``. Eine
    Sperre, die je nach Login-Formular andere Konsequenzen haette, waere genau
    die Asymmetrie, die dieses Ticket schliesst.
    """

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)

        # Lazy: ``core.admin_site`` wird beim App-Start vor ``AppConfig.ready()``
        # importiert (die ModelAdmins registrieren sich an dieser Site).
        from core.models import AuditLog
        from core.services.audit import log_audit_event
        from core.services.security import RevocationReason, is_locked, revoke_credentials
        from core.signals.audit import get_client_ip

        # N9 (Refs #1446): Lockout je Quell-IP — fremde Falschpassworter sperren
        # den Admin-Login des Opfers von dessen eigener IP nicht.
        ip_address = get_client_ip(self.request)
        if not is_locked(user, ip_address=ip_address):
            return

        # R18 (Refs #1476/#1666): Der Zweig feuert NUR bei korrektem Passwort
        # gegen einen gesperrten Account — nicht angreifer-getrieben.
        revoke_credentials(user, reason=RevocationReason.ACCOUNT_LOCKOUT, request=self.request)
        log_audit_event(
            self.request,
            AuditLog.Action.LOGIN_FAILED,
            user=user,
            facility=getattr(user, "facility", None),
            detail={
                "message": "Login blockiert durch Account-Lockout",
                "username": user.username,
                "reason": "locked",
            },
        )
        raise ValidationError(
            _(
                "Ihr Konto ist nach mehreren fehlgeschlagenen Versuchen "
                "temporär gesperrt. Bitte später erneut versuchen oder "
                "Administration kontaktieren."
            ),
            code="locked",
        )


class AnlaufstelleAdminSite(UnfoldAdminSite):
    """AdminSite mit Rollen-Gate (super_admin/facility_admin) + Sudo-Pflicht.

    Erbt von ``UnfoldAdminSite``, damit das Unfold-Theme (Search-Endpoint,
    each_context-Variablen, Login-Form) ohne Brueche funktioniert.
    """

    site_header = "Anlaufstelle Verwaltung"
    site_title = "Anlaufstelle"
    index_title = "Datenverwaltung"
    # Refs #2020: Lockout-Pruefung am Admin-Login. ``UnfoldAdminSite.__init__``
    # setzt seine eigene Form nur, wenn hier ``None`` steht — die Unfold-
    # Widget-Klassen erben wir ueber die Basisklasse.
    login_form = AdminLockoutAuthenticationForm

    @staticmethod
    def _has_admin_role(user) -> bool:
        """Single source of truth: hat der User eine Admin-Rolle?"""
        if not user.is_authenticated:
            return False
        return user.is_super_admin or user.is_facility_admin

    def has_role_permission(self, request) -> bool:
        """Public API fuer ModelAdmin-Mixins.

        ModelAdmin-Klassen rufen ``self.admin_site.has_role_permission(request)``
        in ``has_view/add/change/delete_permission`` auf — so wird die Rollen-
        Logik nur an einer Stelle gepflegt.
        """
        return self._has_admin_role(request.user)

    def has_super_admin_permission(self, request) -> bool:
        """Public API fuer ModelAdmin-Mixins: nur super_admin (A2.3, Refs #1021).

        Fuer installationsweite Modelle oberhalb der Facility-Ebene (z.B.
        ``Organization``), die ein facility_admin nicht verwalten darf. Single
        Source of Truth analog ``has_role_permission``/``assignable_roles``.
        """
        user = request.user
        return user.is_authenticated and user.is_super_admin

    def scope_to_facility(self, queryset, request):
        """Public API fuer ModelAdmin.get_queryset() bei facility-gescopten Models.

        super_admin sieht alles, facility_admin sieht nur ``request.current_facility``.
        Konsistent mit ``FacilityScopedManager`` in den Models.
        """
        if request.user.is_super_admin:
            return queryset
        return queryset.filter(facility=request.current_facility)

    def assignable_roles(self, request):
        """Welche Rollen darf der eingeloggte Admin vergeben? (A2.1, Refs #1020).

        super_admin vergibt alle Rollen; facility_admin alles **ausser**
        super_admin — verhindert Privilege-Escalation auf die installationsweite
        super_admin-Rolle. Single Source of Truth fuer die Rollen-Hierarchie
        (analog ``scope_to_facility``); konsumiert von
        ``UserAdmin.formfield_for_choice_field('role')``.
        """
        from core.models import User

        if request.user.is_super_admin:
            return list(User.Role.choices)
        return [choice for choice in User.Role.choices if choice[0] != User.Role.SUPER_ADMIN]

    def has_permission(self, request):
        """Zugriff nur fuer super_admin/facility_admin mit aktivem Sudo-Mode."""
        if not self._has_admin_role(request.user):
            return False
        if not getattr(settings, "SUDO_MODE_ENABLED", True):
            return True
        return is_in_sudo(request)

    @method_decorator(ratelimit(key="ip", rate="5/m", method="POST", block=True))
    @method_decorator(ratelimit(key=_admin_login_username_key, rate="10/h", method="POST", block=True))
    def login(self, request, extra_context=None):
        """Login-View: wenn User eingeloggt + Rolle OK, aber Sudo fehlt -> /sudo/.

        Refs #2020: Zwei-Ebenen-Ratelimit auf dem POST, identisch zum App-Login
        (``CustomLoginView``, Refs #598) — 5/m je IP gegen klassischen
        Brute-Force, 10/h je Username gegen verteilte Versuche von rotierenden
        IPs. Bis hierher delegierte diese Methode ungebremst an Djangos
        Stock-Login; die nachgelagerten Gates (sofort pflichtige MFA,
        Sudo-Mode) begrenzten den Schaden, aber Raten gegen dieses Formular war
        gratis, und die Fehlversuche fuellen dieselben ``LOGIN_FAILED``-Zeilen,
        aus denen Lockout und Breach-Detection ihre Signale ziehen.

        ``block=True`` wirft ``Ratelimited``; ``handler403``
        (``core/views/errors.py``) beantwortet das mit HTTP 429 (Refs #1354).
        Die Eimer sind von denen des App-Logins getrennt (django-ratelimit
        gruppiert je dekorierter Funktion) — jede der beiden Flaechen traegt
        ihre eigene Schranke, statt dass ein Angriff auf die eine die andere
        fuer legitime Anmeldungen zusperrt.

        Der ``method="POST"``-Filter haelt die Login-SEITE erreichbar: ein GET
        zaehlt nicht mit.
        """
        if (
            self._has_admin_role(request.user)
            and getattr(settings, "SUDO_MODE_ENABLED", True)
            and not is_in_sudo(request)
        ):
            return redirect(f"/sudo/?next={request.get_full_path()}")
        return super().login(request, extra_context)


# Namespace "admin" — Unfold-Theme-Templates verwenden {% url 'admin:...' %},
# das nur mit diesem Namespace aufgeloest werden kann. Das ist OK, weil wir die
# Default-django.contrib.admin.site aus dem URLconf entfernen (kein Konflikt).
anlaufstelle_admin_site = AnlaufstelleAdminSite(name="admin")
