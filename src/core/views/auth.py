"""Custom auth views for Anlaufstelle."""

import logging

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.http import require_http_methods, require_POST
from django.views.i18n import set_language
from django_ratelimit import UNSAFE

from core.constants import RATELIMIT_MUTATION
from core.middleware.session_idle import ABSOLUTE_DEADLINE_KEY, LAST_RENEWAL_KEY
from core.models import AuditLog, User
from core.models.settings import DEFAULT_SESSION_ABSOLUTE_HOURS
from core.services.audit import hmac_hash_email, log_audit_event
from core.services.security import RevocationReason, invite_token_generator, is_locked, revoke_credentials
from core.services.security.origin import external_link_context
from core.services.security.session_rotation import rotate_session_after_privilege_gain
from core.signals.audit import get_client_ip
from core.utils.ratelimit import ratelimit
from core.views.mixins import anonymous_session_required
from core.views.utils import is_get_navigable, safe_redirect_path

logger = logging.getLogger(__name__)


def resolve_post_login_url(request, redirect_to: str | None) -> str:
    """Resolve a safe post-authentication target with the role default.

    ``next`` values can originate from login or MFA requests, so validate the
    target at this shared boundary even if an upstream Django view already did
    so. A root target is the implicit default for super admins, not a deliberate
    deep link (Refs #970/#2206). A target that cannot be reached by a GET
    navigation is dropped the same way (Refs #2561).
    """
    is_safe = url_has_allowed_host_and_scheme(
        url=redirect_to or "",
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    )
    # A POST-only endpoint (e.g. an HTMX mutation interrupted by an expired
    # session) cannot be resumed by navigation; it falls back to the role default.
    safe_redirect_to = redirect_to if is_safe and is_get_navigable(redirect_to or "") else None
    if getattr(request.user, "is_super_admin", False):
        if safe_redirect_to and safe_redirect_to not in ("/", settings.LOGIN_REDIRECT_URL):
            return safe_redirect_to
        return "/system/"
    return safe_redirect_to or settings.LOGIN_REDIRECT_URL


def _login_username_key(group, request):
    """Ratelimit-Key aus Username: lowercased + gestrippt, damit 'Alice',
    ' alice ' und 'alice' nicht als drei unabhängige Buckets zählen."""
    return (request.POST.get("username") or "").lower().strip()


@method_decorator(ratelimit(key="ip", rate="5/m", method=UNSAFE, block=True), name="dispatch")
@method_decorator(ratelimit(key=_login_username_key, rate="10/h", method=UNSAFE, block=True), name="dispatch")
class CustomLoginView(auth_views.LoginView):
    """Login with session timeout from facility settings.

    Zwei-Ebenen-Ratelimit (Refs #598):
    - IP-Limit (5/m): schützt vor klassischem Brute-Force von einer IP.
    - Username-Limit (10/h): schützt vor verteilten Angriffen auf einen Account
      (Botnet mit rotierenden IPs). Ein echter Nutzer tippt nicht 10× in einer
      Stunde das falsche Passwort — dann geht er auf Password-Reset.
    """

    template_name = "auth/login.html"

    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        """Refs #867 + #970: super_admin landet im /system/-Dashboard.

        Default-Verhalten (``LOGIN_REDIRECT_URL`` bzw. ``?next=``) bleibt
        fuer alle anderen Rollen erhalten — nur super_admin hat keinen
        Facility-Kontext, wuerde also auf einer Facility-gescopten
        Default-Seite (Zeitstrom) eine leere Sicht sehen.

        Refs #970: ``?next=/`` (bzw. ``LOGIN_REDIRECT_URL``) gilt NICHT als
        gezielter Deep-Link, sondern als impliziter Default. Hintergrund:
        ein unauth super_admin, der ``/`` aufruft, wird auf
        ``/login/?next=/`` umgeleitet (ZeitstromView ist LoginRequiredMixin).
        Ohne diese Sonderbehandlung uebersteuert das implizite ``?next=/``
        die ``/system/``-Logik systematisch im Normal-Login.
        """
        return resolve_post_login_url(self.request, self.get_redirect_url())

    def form_invalid(self, form):
        count = getattr(self.request, "super_admin_failed_login_count", 0)
        if count:
            from django_ratelimit.exceptions import Ratelimited

            from core.services.security.login_lockout import LOCKOUT_THRESHOLD
            from core.views.errors import permission_denied

            # Retry advice only: failed passwords never occupy sleeping workers
            # or establish a lock that rejects a subsequent valid password.
            self.request.rate_limit_reason = "super_admin_backoff"
            response = permission_denied(self.request, Ratelimited())
            response["Retry-After"] = str(2 ** min(count - LOCKOUT_THRESHOLD, 3))
            return response
        return super().form_invalid(form)

    def form_valid(self, form):
        # Account-Lockout (Refs #612): Nach 10 Fehlversuchen in 15 Minuten
        # auch bei korrektem Passwort sperren. Der View-Level-Ratelimit
        # schützt bereits vor Spam-Versuchen, der Lockout verhindert das
        # „nach Cooldown sofort wieder durchkommen"-Muster.  Der Check greift
        # VOR super().form_valid() — an dieser Stelle hat AuthenticationForm
        # den User zwar geprüft, aber auth_login() noch nicht ausgeführt; es
        # existiert also noch keine gebundene Session, die wir revoken müssten.
        user = form.get_user()
        # N9 (Refs #1446): Lockout je Quell-IP prüfen — fremde Falschpasswörter
        # sperren nicht den Login des Opfers von dessen eigener IP. Der folgende
        # "reason=locked"-Audit-Write trägt (über log_audit_event) die Request-IP
        # mit, sodass der Block je Quell-IP self-sustaining bleibt (gewollt).
        if is_locked(user, ip_address=get_client_ip(self.request)):
            # R18 (Refs #1476/#1666): Ein gesperrter Account ist ein Revocation-
            # Ereignis — sein Offline-Bundle-Schlüssel (und etwaige noch aktive
            # Fremd-Sessions) dürfen die Sperre nicht überdauern. Salt rotieren +
            # Sessions flushen wie bei Rollenwechsel/Deaktivierung. Der Zweig
            # feuert NUR bei korrektem Passwort gegen einen gesperrten Account
            # (AuthenticationForm hat validiert) → nicht angreifer-getrieben:
            # wer das Passwort nicht kennt, erreicht ihn nie. Refs #1965: der
            # Widerruf laeuft ueber dieselbe Funktion wie die drei
            # Passwort-Pfade; die Wirkungs-Matrix des Lockout-Grundes waehlt
            # Rotation + Flush, aber keine Passwort-Nebenwirkungen und keine
            # eigene Audit-Zeile (der folgende LOGIN_FAILED-Write ist die Spur).
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
            form.add_error(
                None,
                _(
                    "Ihr Konto ist nach mehreren fehlgeschlagenen Versuchen "
                    "temporär gesperrt. Bitte später erneut versuchen oder "
                    "Administration kontaktieren."
                ),
            )
            return self.form_invalid(form)

        # Refs #2067: archivierte Einrichtung sperrt die Anmeldung. Muster wie
        # der Lockout-Zweig darueber — VOR ``super().form_valid()``, es
        # existiert also noch keine Session. ``AuthenticationForm`` hat nur
        # ``User.is_active`` geprueft; ``Facility.is_active`` war bis hierher
        # wirkungslos. Die Middleware faengt zusaetzlich die bereits laufenden
        # Sitzungen ab — beide Ebenen zusammen ergeben „archiviert heisst
        # gesperrt", nicht „gesperrt ab dem naechsten Login".
        # ``super_admin`` ist ausgenommen: die Rolle IST der Reaktivierungspfad
        # (``FacilityAdmin`` ist super-only, L1/Refs #1375). Kein
        # Credential-Widerruf wie beim Lockout — Archivieren ist ein
        # organisatorischer Vorgang, kein Kompromittierungsverdacht.
        login_facility = getattr(user, "facility", None)
        if login_facility is not None and not login_facility.is_active and not getattr(user, "is_super_admin", False):
            log_audit_event(
                self.request,
                AuditLog.Action.LOGIN_FAILED,
                user=user,
                facility=login_facility,
                detail={
                    "message": "Login blockiert — Einrichtung archiviert",
                    "username": user.username,
                    "reason": "facility_inactive",
                },
            )
            form.add_error(
                None,
                _(
                    "Ihre Einrichtung ist archiviert. Eine Anmeldung ist "
                    "derzeit nicht möglich. Bitte wenden Sie sich an die "
                    "Systemverwaltung."
                ),
            )
            return self.form_invalid(form)

        response = super().form_valid(form)
        # Load session timeout from facility settings
        facility = getattr(user, "facility", None)
        # Refs #1868: Default fuer alle, die keine Facility-Settings haben
        # (super_admin ohne Einrichtung, Einrichtung ohne Settings-Zeile) —
        # „kein Cap" ist hier bewusst KEINE Option.
        absolute_hours = DEFAULT_SESSION_ABSOLUTE_HOURS
        if facility is not None:
            try:
                facility_settings = facility.settings
                timeout = facility_settings.session_timeout_minutes * 60
                self.request.session.set_expiry(timeout)
                absolute_hours = facility_settings.session_absolute_hours
            except facility._meta.model.settings.RelatedObjectDoesNotExist:
                pass  # No settings -> default session timeout
        # Refs #1858: Erneuerungs-Zeitstempel des Inaktivitaets-Fensters direkt
        # mitstempeln — der Login schreibt die Session ohnehin, so loest der
        # erste Folge-Request keinen zusaetzlichen Session-Write aus.
        from django.utils import timezone

        now = timezone.now().timestamp()
        self.request.session[LAST_RENEWAL_KEY] = now
        # Refs #1868: absolutes Sitzungs-Ende EINMAL einfrieren. Bewusst hier
        # und nicht in der Middleware: ``facility.settings`` ist an dieser
        # Stelle ohnehin geladen (keine Extra-Query), und die Frist muss ab
        # dem Login-Zeitpunkt laufen — nicht ab dem ersten Folge-Request. Ein
        # spaeter geaenderter Settings-Wert wirkt deshalb erst ab dem naechsten
        # Login (so dokumentiert im help_text des Feldes).
        self.request.session[ABSOLUTE_DEADLINE_KEY] = now + absolute_hours * 3600
        # 2FA: Die Session startet immer unverifiziert — die MFA-Middleware
        # leitet bei Bedarf nach /mfa/verify/ oder /mfa/setup/ weiter.
        self.request.session["mfa_verified"] = False
        # Grace-Frist fuer den Rollen-MFA-Zwang beim ERSTEN Login stempeln
        # (Refs #689): now + 30 Tage. Nur setzen, wenn noch keine Frist steht —
        # Folge-Logins verlaengern die Frist nicht. Rollen-unabhaengig gestempelt,
        # damit ein spaeter aktivierter Rollen-Zwang eine deterministische Frist
        # vorfindet. Das Feld wird aber nur fuer die grace-behafteten Rollen
        # (staff/assistant [, lead], Refs #689) konsultiert — privilegierte Rollen
        # (super_admin/facility_admin) werden ohne Geraet SOFORT umgeleitet, ihre
        # gestempelte Frist bleibt wirkungslos.
        if user.mfa_setup_deadline is None:
            from datetime import timedelta

            from django.utils import timezone

            from core.constants import MFA_SETUP_GRACE_DAYS

            user.mfa_setup_deadline = timezone.now() + timedelta(days=MFA_SETUP_GRACE_DAYS)
            user.save(update_fields=["mfa_setup_deadline"])
        return response


@method_decorator(require_http_methods(["GET", "HEAD", "POST", "OPTIONS"]), name="dispatch")
class CustomLogoutView(auth_views.LogoutView):
    """Standard logout with redirect to login and site data clearing."""

    next_page = "/login/"

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        # Refs #573 + #1065: "storage" räumt IndexedDB/localStorage ab,
        # "cache" zusätzlich den HTTP-Cache (gecachte Art.-9-Responses).
        response["Clear-Site-Data"] = '"storage", "cache"'
        return response


# Refs #2391: the request writes an audit row for the account of the entered
# address, possibly in another facility; a signed-in session gets a notice.
@method_decorator(ratelimit(key="ip", rate="5/m", method=UNSAFE, block=True), name="dispatch")
@method_decorator(anonymous_session_required, name="dispatch")
class RateLimitedPasswordResetView(auth_views.PasswordResetView):
    """Password reset with rate limiting and audit logging."""

    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        """Audit each accepted reset request (Refs #598).

        Response-Verhalten bleibt identisch: gleiche Antwort egal ob die Email
        einem Account zugeordnet ist (Anti-Enumeration). Im Audit-Log wird der
        User nur eingetragen, wenn genau ein eindeutiger Treffer existiert —
        das ist für Admin-Forensik relevant, ohne PII-Enumeration via Logs.
        """
        email = form.cleaned_data.get("email", "")
        matched_user = None
        facility = None
        try:
            matched_user = User.objects.filter(email__iexact=email, is_active=True).first()
            if matched_user is not None:
                facility = getattr(matched_user, "facility", None)
        except Exception:
            # Auth-Signal-Receiver dürfen den Flow nie kippen.
            logger.exception("Password-Reset User-Lookup fehlgeschlagen")

        # Refs #791: Klartext-E-Mails im append-only AuditLog widersprechen
        # DSGVO-Datenminimierung. Stattdessen HMAC-Hash schreiben — Lookup
        # bei bekannter E-Mail bleibt moeglich (gleiche E-Mail -> gleicher
        # Hash), eingegebene Adressen leben aber nicht 24 Monate weiter.
        log_audit_event(
            self.request,
            AuditLog.Action.PASSWORD_RESET_REQUESTED,
            user=matched_user,
            facility=facility,
            target_type="User" if matched_user else "",
            target_id=str(matched_user.pk) if matched_user else "",
            detail={"email_hash": hmac_hash_email(email)} if email else {},
        )

        # Refs #1967: Protokoll und Host des Reset-Links kommen aus
        # EXTERNAL_ORIGIN, nicht aus ``request.is_secure()``/der aktuellen
        # Site. ``extra_email_context`` ist dafuer der richtige Hebel — es wird
        # in ``PasswordResetForm.save()`` zuletzt in den Mail-Kontext gemergt
        # und schlaegt damit beide abgeleiteten Werte. ``domain_override``
        # ueberschriebe dagegen nur ``domain`` und liesse ``protocol`` am
        # Request haengen.
        self.extra_email_context = {**(self.extra_email_context or {}), **external_link_context()}
        return super().form_valid(form)


# Refs #2034: ``View.as_view()`` kopiert ``cls.dispatch.__dict__`` auf die
# View-Funktion. Djangos ``PasswordResetConfirmView`` markiert ihr ``dispatch``
# per ``login_not_required`` mit ``login_required = False``; der eigene
# ``dispatch``-Override unten bringt kein solches ``__dict__`` mit und wuerde die
# Markierung sonst verschlucken. Ohne sie sperrte eine spaeter eingehaengte
# ``LoginRequiredMiddleware`` Reset und Invite ausgerechnet fuer Abgemeldete —
# also fuer die Gruppe, fuer die es beide Wege ueberhaupt gibt. ``never_cache``
# und ``sensitive_post_parameters`` der Elternklasse umschliessen weiterhin den
# ``super()``-Aufruf und brauchen keine Wiederholung.
# Refs #2391: the inner decorator wraps the own ``dispatch`` below, so a
# signed-in session gets its notice before the session token is read, stored
# or rotated; the outer limit preserves the ``login_not_required`` marker.
@method_decorator(ratelimit(key="ip", rate="5/m", method=UNSAFE, block=True), name="dispatch")
@method_decorator(login_not_required, name="dispatch")
@method_decorator(anonymous_session_required, name="dispatch")
class CustomPasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    """Password-Reset, der zugleich einen aktiven Account-Lockout aufhebt (Refs #869).

    Ein erfolgreich abgeschlossener Reset (gueltiges Token + neues Passwort)
    ist ein starker Vertrauensanker: der User hat E-Mail-Kontrolle bewiesen
    und ein neues Passwort gesetzt. Daher schreiben wir einen LOGIN_UNLOCK-
    AuditLog — nachfolgende ``is_locked()``-Pruefungen ignorieren alle
    LOGIN_FAILED-Eintraege bis zu diesem Zeitpunkt.

    Aus demselben Vertrauensanker faellt hier auch ``must_change_password``
    (Refs #1969). Das Flag setzt die Benutzeranlage im Admin bei *jedem* neuen
    Konto; aufgeloest wurde es bisher nur im Change-Pfad. Ein frisch
    eingeladener Nutzer landete deshalb beim ersten Login sofort wieder auf
    ``/password-change/`` und musste das eben selbst gewaehlte Passwort ein
    zweites Mal ersetzen. Der E-Mail-lose Fallback (Klartext-Initialpasswort
    aus der Admin-Anlage) erzwingt den Wechsel weiterhin: er kommt hier gar
    nicht vorbei, solange keine Adresse nachgetragen und der Setup- bzw.
    Reset-Weg tatsaechlich durchlaufen wird.
    """

    #: Refs #1965: Ausloeser-Slug fuer den zentralen Credential-Widerruf.
    #: ``InviteConfirmView`` ueberschreibt ihn — dieselbe Mechanik, aber im
    #: Protokoll unterscheidbar (Ersteinrichtung vs. Zuruecksetzen).
    revocation_reason = RevocationReason.PASSWORD_RESET

    def dispatch(self, *args, **kwargs):
        """Refs #2034: Der Token-Uebergang ist eine Rechteanhebung — rotieren.

        Djangos ``PasswordResetConfirmView.dispatch`` legt ein gueltiges Token
        unter ``auth_views.INTERNAL_RESET_SESSION_TOKEN`` in die **bestehende** Sitzung und
        leitet auf den tokenfreien ``set-password``-Pfad um. Ab diesem Moment
        darf die Sitzung ein neues Passwort setzen, ohne den Link je gesehen zu
        haben — ein vorher entwendetes oder zurueckgelassenes Cookie erbt genau
        das (Cross-Review, R-A1-10).

        Erkannt wird der Uebergang am neu erschienenen Session-Eintrag, statt
        die Token-Pruefung der Elternklasse zu duplizieren (eine zweite Kopie
        der Pruefung waere die groessere Fehlerquelle). Die Rotation laeuft
        damit zwar nach dem In-Memory-Write, aber vor JEDEM Persistieren und vor
        der Antwort: ``cycle_key()`` schreibt die Daten unter der NEUEN ID und
        loescht die alte Zeile — es existiert zu keiner Zeit eine gespeicherte
        Sitzung unter der alten ID, die das Token traegt.

        Das ``finally`` ist dabei die eigentliche Sicherheitszusage und kein
        Stilmittel (R-A1-12): die Elternklasse schreibt das Token und
        baut ERST DANACH den Redirect. Wirft dieser Schritt — der Reviewer hat
        es ueber einen ueberlangen, weiterhin dekodierbaren Reset-Pfad und den
        daraus folgenden ``DisallowedRedirect`` gezeigt —, entsteht trotzdem
        eine Antwort (400 bzw. 500), und die ``SessionMiddleware`` speichert die
        Sitzung ausgangsseitig **mit** dem Token. Ohne ``finally`` bliebe genau
        dieser Fall unrotiert: das Token laege unter der ALTEN ID, und ein
        zweiter Client mit dem alten Cookie koennte damit das Passwort setzen.
        Die Ausnahme wird nach der Rotation unveraendert weitergereicht.

        Fehlschlaege (ungueltiges/abgelaufenes Token, unbekannte UID, Abbruch
        VOR dem Write) rotieren bewusst nicht: sie heben nichts an, und eine
        Rotation waere dort ein kostenloser Sitzungs-Reset fuer jeden
        Cookie-Besitzer.

        ``InviteConfirmView`` erbt diesen Ablauf mit dem eigenen
        ``token_generator`` unveraendert mit.
        """
        session = self.request.session
        token_before = session.get(auth_views.INTERNAL_RESET_SESSION_TOKEN)
        try:
            return super().dispatch(*args, **kwargs)
        finally:
            token_after = session.get(auth_views.INTERNAL_RESET_SESSION_TOKEN)
            if token_after is not None and token_after != token_before:
                rotate_session_after_privilege_gain(self.request)

    def form_valid(self, form):
        response = super().form_valid(form)
        user = form.user
        if user is not None and getattr(user, "pk", None):
            revoke_credentials(user, reason=self.revocation_reason, request=self.request)
        return response


class InviteConfirmView(CustomPasswordResetConfirmView):
    """Setup-Seite fuer Einladungen (L4, Refs #1375).

    Identisch zum Passwort-Reset-Confirm (neues Passwort setzen + Lockout
    aufheben), nutzt aber den entkoppelten ``invite_token_generator`` — Invite-
    Tokens haben einen eigenen, laengeren Ablauf (``INVITE_TOKEN_TIMEOUT``) und
    sind NICHT auf der Passwort-Reset-Route verwendbar (eigenes ``key_salt``)."""

    token_generator = invite_token_generator
    revocation_reason = RevocationReason.INVITE_SETUP


@method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method=UNSAFE, block=True), name="dispatch")
class CustomPasswordChangeView(auth_views.PasswordChangeView):
    """Password change with must_change_password reset and offline-salt rotation."""

    template_name = "auth/password_change.html"
    success_url = "/"

    def form_valid(self, form):
        response = super().form_valid(form)
        # Refs #1965: Salt-Rotation, Sudo-Clear, Zwangswechsel-Flag und die
        # Rechenschaftszeile liegen in der zentralen Funktion — der Wechsel ist
        # nur noch ein Ausloeser unter mehreren. Die Rotation ist hier bewusst
        # scharf (und bei Reset/Invite bewusst nicht): dieser Pfad haelt die
        # eigene Session per ``update_session_auth_hash`` am Leben, der
        # abgeleitete Offline-Schluessel muss also aktiv entwertet werden.
        revoke_credentials(self.request.user, reason=RevocationReason.PASSWORD_CHANGE, request=self.request)
        return response


class OfflineKeySaltView(LoginRequiredMixin, View):
    """Deliver the per-user PBKDF2 salt for client-side AES-GCM key derivation.

    The salt is the only server-side artefact of the offline encryption layer.
    POST-only because clients call it after login, not via navigation; this
    keeps it out of the browser history and lets us audit each fetch.
    """

    @method_decorator(ratelimit(key="user", rate="10/m", method="POST", block=True))
    def post(self, request, *args, **kwargs):
        if request.headers.get("X-Demo-Offline-Bootstrap") == "1":
            from core.demo import DEMO_OFFLINE_BOOTSTRAP_ERROR, DEMO_PASSWORD

            # Recheck in case the password changed after the demo-start redirect.
            if not getattr(settings, "DEMO_MODE", False) or not request.user.check_password(DEMO_PASSWORD):
                return JsonResponse({"error": str(DEMO_OFFLINE_BOOTSTRAP_ERROR)}, status=403)
        salt = request.user.ensure_offline_key_salt()
        log_audit_event(
            request,
            AuditLog.Action.OFFLINE_KEY_FETCH,
            target_obj=request.user,
            facility=request.user.facility,
            detail={"event": "offline_key_salt_fetched"},
        )
        # Refs #867: super_admin landet im /system/-Bereich, andere Rollen
        # auf der Default-Startseite (Zeitstrom). Wird hier mitgegeben, weil
        # auth-bootstrap.js den Login-Redirect mit ``redirect: "manual"``
        # liest und der Location-Header bei opaqueredirect-Responses nicht
        # auslesbar ist (Fetch-API-Spec, same-origin). Vorher: Fallback ""/"".
        # The scripted login also needs the deep link a session expiry interrupted:
        # ``?next=`` (sent by auth-bootstrap.js from the login URL) is resolved with
        # the same boundary check as the native redirect (Refs #2561).
        home_url = resolve_post_login_url(request, safe_redirect_path(request.GET.get("next")))
        from core.services.security.offline_keys import offline_key_max_age

        return JsonResponse(
            {
                "salt": salt,
                "home_url": home_url,
                "offline_key_max_age": offline_key_max_age(request.user.facility),
            }
        )


@require_POST
@ratelimit(key="user_or_ip", rate=RATELIMIT_MUTATION, method="POST", block=True)
def set_user_language(request):
    """Save language preference to user model, then delegate to Django's set_language."""
    language = request.POST.get("language")
    if language and request.user.is_authenticated:
        valid_langs = [code for code, _ in settings.LANGUAGES]
        if language in valid_langs:
            User.objects.filter(pk=request.user.pk).update(preferred_language=language)
    return set_language(request)
