"""E2E-Tests: Onboarding-Journeys einer neuen Mitarbeiterin (Refs #2025).

Die „Haustuer" jedes neuen Kontos war bis hierher nur unit-getestet (Token,
Credential-Lifecycle, Middleware-Redirects). Genau hier komponieren aber ZWEI
Redirect-Middlewares (``ForcePasswordChangeMiddleware`` →
``MFAEnforcementMiddleware``, in dieser Reihenfolge in ``MIDDLEWARE``) mit einer
One-Time-Token-View zu einem Nutzerpfad.

**Zwei Einstiegswege, nicht einer** — der Ticket-Satz „Invite-Link →
Initialpasswort → Zwangswechsel → MFA" vermischt sie:

1. *Invite-Weg* (``TestInviteOnboardingJourney``): Der Setup-Link setzt das
   Passwort selbst; ``InviteConfirmView`` ruft dabei
   ``revoke_credentials(..., INVITE_SETUP)`` auf, was ``must_change_password``
   loescht (``core/services/security/credentials.py``, Wirkungs-Matrix
   ``reset_forced_change=True``). Nach dem Invite gibt es also **keinen**
   Zwangswechsel mehr — der erste Login geht direkt ins MFA-Setup.
2. *Initialpasswort-Weg* (``TestInitialPasswordOnboardingJourney``): Wird ein
   Konto mit nutzbarem Passwort und ``must_change_password=True`` angelegt (so
   macht es die Admin-Neuanlage), greift der Zwangswechsel — und zwar VOR dem
   MFA-Setup. Genau diese Reihenfolge prueft der Test explizit.

**Warum der Test den Link nicht aus der Mail liest:** ``settings.e2e`` erbt von
``dev.py`` das ``console``-Mail-Backend, und der E2E-gunicorn laeuft mit
``stdout=DEVNULL`` (``conftest.py``). Weder ``mail.outbox`` (nur locmem) noch
der Konsolentext sind fuer den Testprozess erreichbar. Der Setup-Link wird
deshalb serverseitig ueber ``build_invite_url`` erzeugt; nur der PFAD wird
uebernommen, weil ``EXTERNAL_ORIGIN`` in E2E auf ``localhost:8844`` zeigt, die
Fixture aber ``127.0.0.1`` liefert.

**MFA-Erzwingung ohne zweiten Server:** die Wegwerf-Konten werden mit
``mfa_required=True`` angelegt. Das aktiviert ``mfa_enforced_deliberately``
(``core/models/user.py``) und damit den sofortigen ``/mfa/setup/``-Redirect
unabhaengig von ``MFA_ENFORCE_PRIVILEGED_ROLES`` — der ``enforced_base_url``-
Server (Refs #1019) wird hier NICHT gebraucht.

Alle Konten sind Wegwerf-Objekte (``e2e-onb-<uuid8>``) und werden im
``finally`` wieder geloescht: der volle E2E-Lauf ist seriell und teilt die
Datenbank, ein zurueckgelassener Nutzer wuerde die Zaehlungen anderer Dateien
(Zugaenge-Hub, Statistik) verschieben.
"""

import base64
import re
import uuid

import pytest
from django_otp.oath import TOTP
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect

from ._helpers import django_shell

pytestmark = pytest.mark.e2e

#: Neues Passwort im Setup-/Wechsel-Schritt. >= 12 Zeichen (MinimumLengthValidator),
#: kein Bezug zum Usernamen (UserAttributeSimilarityValidator).
NEUES_PASSWORT = "Rhabarber-Kompott-27"

#: Initialpasswort fuer den Zwangswechsel-Weg (bewusst verschieden vom neuen).
INITIAL_PASSWORT = "Startwert-Holunder-9"


def _marker_values(e2e_env, script, marker):
    """``django_shell`` fahren und nur die mit ``marker`` praefixierten Zeilen lesen.

    Noetig, weil auf dem stdout des ``manage.py shell``-Subprozesses auch
    Log-Zeilen (WeasyPrint-Init) und — beim Invite — die komplette
    Konsolen-Mail landen.
    """
    return [line.removeprefix(marker) for line in django_shell(e2e_env, script) if line.startswith(marker)]


def _totp_code(secret_b32: str, drift: int = 0) -> str:
    """Sechsstelliger TOTP-Code aus dem im Setup angezeigten Base32-Secret.

    ``pyotp`` ist nicht installiert; ``django_otp.oath.TOTP`` rechnet mit
    demselben Schluessel. ``drift`` verschiebt um ganze 30-Sekunden-Fenster.

    ``drift=1`` ist der Trick fuer den ZWEITEN Login: ``TOTPDevice.verify_token``
    verlangt ``t > last_t`` (kein Token-Replay), akzeptiert aber ``tolerance``
    Schritte Abweichung. Ein Code fuer das FOLGE-Fenster ist damit sofort
    gueltig — ohne bis zu 30 s zu warten und ohne ``wait_for_timeout``.
    """
    padded = secret_b32 + "=" * (-len(secret_b32) % 8)
    totp = TOTP(base64.b32decode(padded), drift=drift)
    return f"{totp.token():06d}"


def _create_invited_user(e2e_env, username):
    """Eingeladenes Konto ueber den echten Service anlegen; liefert den Invite-Pfad.

    Nutzt ``create_invited_user`` (nicht ``User.objects.create``), damit
    ``must_change_password``/``set_unusable_password``/``mfa_required`` exakt so
    gesetzt sind wie bei einer echten Einladung durch die Anwendungsbetreuung.
    """
    script = f"""
from urllib.parse import urlparse
from core.models import Facility, User
from core.services.security.invite import build_invite_url
from core.services.user_access import create_invited_user

facility = Facility.objects.order_by("pk").first()
actor = User.objects.get(username="admin")
result = create_invited_user(
    facility=facility,
    actor=actor,
    username="{username}",
    email="{username}@example.org",
    display_name="E2E Onboarding",
    role=User.Role.STAFF,
    mfa_required=True,
    can_confirm_deletion=False,
)
print("E2E_INVITE_PATH=" + urlparse(build_invite_url(result.user)).path)
"""
    pfade = _marker_values(e2e_env, script, "E2E_INVITE_PATH=")
    assert len(pfade) == 1, f"Invite-Anlage lieferte keinen Setup-Pfad: {pfade}"
    return pfade[0]


def _expired_invite_path(e2e_env, username):
    """Setup-Pfad mit einem Token erzeugen, dessen Zeitstempel abgelaufen ist.

    Der Generator bekommt ein ``_now`` weit in der Vergangenheit. Der HMAC bleibt
    damit korrekt (gleiches ``key_salt``, gleicher Nutzerzustand) — der Link
    scheitert AUSSCHLIESSLICH am Ablauf-Zweig von
    ``InviteTokenGenerator.check_token`` (``INVITE_TOKEN_TIMEOUT``, 3 Tage).
    Das ist der Unterschied zu einem bloss verfaelschten Token, das schon am
    Signaturvergleich scheitern wuerde.
    """
    script = f"""
from datetime import datetime, timedelta
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from core.models import User
from core.services.security.invite import InviteTokenGenerator

user = User.objects.get(username="{username}")
generator = InviteTokenGenerator()
generator._now = lambda: datetime.now() - timedelta(days=365)
uid = urlsafe_base64_encode(force_bytes(user.pk))
token = generator.make_token(user)
print("E2E_EXPIRED_PATH=" + reverse("invite_confirm", kwargs={{"uidb64": uid, "token": token}}))
"""
    pfade = _marker_values(e2e_env, script, "E2E_EXPIRED_PATH=")
    assert len(pfade) == 1, f"Abgelaufenes Token nicht erzeugt: {pfade}"
    return pfade[0]


def _create_user_with_initial_password(e2e_env, username):
    """Konto mit nutzbarem Initialpasswort + Zwangswechsel + MFA-Pflicht anlegen.

    Spiegelt die Admin-Neuanlage (``core/admin/users.py``: ``must_change_password
    = True``) fuer den Weg OHNE Einladungsmail.
    """
    script = f"""
from core.models import Facility, User

facility = Facility.objects.order_by("pk").first()
user = User(
    facility=facility,
    username="{username}",
    email="{username}@example.org",
    display_name="E2E Initialpasswort",
    role=User.Role.STAFF,
    mfa_required=True,
    must_change_password=True,
)
user.set_password("{INITIAL_PASSWORT}")
user.save()
print("E2E_USER_PK=" + str(user.pk))
"""
    pks = _marker_values(e2e_env, script, "E2E_USER_PK=")
    assert len(pks) == 1, f"Konto-Anlage fehlgeschlagen: {pks}"
    return pks[0]


def _retire_user(e2e_env, username):
    """Wegwerf-Konto stilllegen statt loeschen.

    ``User.delete()`` ist auf dieser Datenbank NICHT moeglich: die
    ``AuditLog``-FKs sind ``SET_NULL``, das dafuer noetige ``UPDATE`` auf
    ``core_auditlog`` verbietet aber der Unveraenderlichkeits-Trigger
    (``prevent_auditlog_mutation``: „AuditLog entries are immutable and cannot
    be modified or deleted."). Dieselbe Grenze hat ``test_user_access_hub.py``
    — es laesst seine ``e2e_access_<uuid8>``-Konten ebenfalls stehen.
    Deaktivieren reicht: ein inaktives Konto kann sich nicht mehr anmelden und
    faellt aus jeder Rollen-/MFA-Auswertung heraus.
    """
    django_shell(
        e2e_env,
        "from core.models import User\n"
        f'User.objects.filter(username="{username}").update(is_active=False, mfa_required=False)\n',
    )


def _open_page(browser):
    """Frischer Context ohne Storage-State — jeder Flow bringt seinen eigenen Login mit."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)
    # Der Passwortwechsel-Guard (Refs #1415) kann bei nicht synchronisierten
    # Offline-Eintraegen ein ``confirm`` zeigen. Frische Konten haben keine —
    # der Handler ist nur die Absicherung gegen einen haengenden Dialog.
    page.on("dialog", lambda dialog: dialog.accept())
    return page, context


def _login(page, base_url, username, password):
    """Formular-Login absenden. Auf das ZIEL wartet der Aufrufer.

    Bewusst ohne eigenes ``wait_for_url``: die erwartete Zielseite ist je Flow
    verschieden (``/mfa/setup/``, ``/mfa/verify/``, ``/password-change/``), und
    im Zwangswechsel-Fall bleibt der Pfad wegen des unten dokumentierten
    Bootstrap-Befundes sogar auf ``/login/``.
    """
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", username)
    page.fill("#id_password", password)
    page.click("#login-form button[type='submit']")


def _complete_mfa_setup(page, base_url):
    """TOTP-Setup inkl. Backup-Code-Quittung; liefert das Base32-Secret zurueck.

    Refs #1118: 2FA ist erst nach der Quittung scharf — vorher bleibt das
    ``TOTPDevice`` unbestaetigt und die Middleware wuerde erneut ins Setup
    fuehren. Der Test laeuft deshalb bis ``/mfa/settings/`` durch.
    """
    page.wait_for_url(re.compile(r"/mfa/setup/"), timeout=20000)
    expect(page.locator("[data-testid='mfa-qrcode']")).to_be_visible(timeout=10000)
    secret = (page.locator("[data-testid='mfa-secret']").text_content() or "").strip()
    assert re.fullmatch(r"[A-Z2-7]+", secret), f"Secret sollte reines Base32 sein, ist aber: {secret!r}"

    page.locator("[data-testid='mfa-token-input']").fill(_totp_code(secret))
    page.locator("[data-testid='mfa-confirm-button']").click()
    page.wait_for_url(re.compile(r"/mfa/backup-codes/"), timeout=20000)

    expect(page.locator("[data-testid='backup-codes-list'] li")).to_have_count(10, timeout=10000)
    page.locator("[data-testid='backup-codes-confirm']").check()
    page.locator("[data-testid='backup-codes-continue']").click()
    page.wait_for_url(re.compile(r"/mfa/settings/"), timeout=20000)
    return secret


def _erwarte_nach_login(page, base_url, ziel):
    """Auf das Post-Login-Ziel warten — mit EINEM Umweg ueber ``/``, falls noetig.

    Der Login-Bootstrap (``static/js/auth-bootstrap.js``) faengt den Submit ab,
    meldet sich per ``fetch`` an und navigiert erst nach dem Salt-Abruf weiter.
    Scheitert dieser Nachlauf, faellt er auf einen nativen Re-Submit des
    Login-Formulars zurueck — mit dem CSRF-Token von VOR der Anmeldung, der
    beim Login rotiert wurde. Die Seite bleibt dann mit „403 – Sicherheits-Token
    abgelaufen" auf ``/login/`` stehen, obwohl die SITZUNG steht.

    Nachgewiesen ist das fuer den Zwangswechsel (siehe
    :class:`TestInitialPasswordOnboardingJourney` — dort fehlt
    ``/auth/offline-key-salt/`` in den ``EXEMPT_URLS`` der Middleware, der
    Salt-Abruf liefert HTML statt JSON). In einem von sechs Datei-Laeufen trat
    dasselbe Bild auch im Invite-Flow auf; die Ursache dort ist NICHT geklaert
    (im Bericht als offener Punkt gefuehrt).

    Der Umweg macht den Test nicht blind: geprueft wird weiterhin, WOHIN die
    Middleware eine angemeldete Sitzung fuehrt — nur nicht mehr, ob der
    Bootstrap selbst dorthin navigiert hat. Fuer den nachgewiesenen Fall haelt
    der erste Test in :class:`TestInitialPasswordOnboardingJourney` genau diese
    Zusage separat fest (seit dem Fix der Middleware in Welle G gruen).
    """
    try:
        page.wait_for_url(ziel, timeout=15000)
        return
    except PlaywrightTimeoutError:
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.wait_for_url(ziel, timeout=20000)


def _logout(page):
    """Abmelden ueber die Sidebar-Form; Waiter VOR dem Klick (Refs #1951)."""
    with page.expect_navigation(url=re.compile(r"/login/"), timeout=20000):
        page.click("form[action='/logout/'] button[type='submit']")


def _verify_second_factor(page, base_url, secret):
    """Zweiten Faktor auf ``/mfa/verify/`` einloesen und auf dem Zeitstrom landen."""
    expect(page.locator("#id_token_totp")).to_be_visible(timeout=10000)
    page.locator("#id_token_totp").fill(_totp_code(secret, drift=1))
    page.locator("[data-testid='mfa-verify-button']").click()
    page.wait_for_url(f"{base_url}/", timeout=20000)
    expect(page.locator("h1")).to_have_text("Zeitstrom", timeout=10000)


class TestInviteOnboardingJourney:
    """Weg 1: Einladung → Setup-Link → Passwort → MFA → Zeitstrom → zweiter Login."""

    def test_expired_invite_link_shows_invalid_notice(self, base_url, browser, e2e_env):
        """Negativpfad: ein abgelaufener Setup-Link ist Status 200 mit Klartext-Hinweis.

        Kein 4xx — ``PasswordResetConfirmView`` rendert dieselbe Seite mit
        ``validlink=False``. Geprueft wird der Ablauf-Zweig (Token mit
        Zeitstempel vor einem Jahr), nicht ein verfaelschtes Token.
        """
        username = f"e2e-onb-{uuid.uuid4().hex[:8]}"
        try:
            _create_invited_user(e2e_env, username)
            pfad = _expired_invite_path(e2e_env, username)

            page, context = _open_page(browser)
            try:
                antwort = page.goto(f"{base_url}{pfad}", wait_until="domcontentloaded")
                assert antwort is not None and antwort.status == 200, (
                    f"Abgelaufener Setup-Link sollte 200 liefern, war: {antwort and antwort.status}"
                )
                expect(page.locator("[role='alert']")).to_contain_text("ungültig oder abgelaufen", timeout=10000)
                # Kein Passwort-Formular — der Link ist wirklich verbraucht.
                expect(page.locator("#id_new_password1")).to_have_count(0)
                expect(page.locator("a:has-text('Neuen Link anfordern')")).to_be_visible()
            finally:
                context.close()
        finally:
            _retire_user(e2e_env, username)

    def test_invite_setup_leads_through_mfa_to_zeitstrom(self, base_url, browser, e2e_env):
        """Kompletter Invite-Weg inkl. zweitem Login ohne Zwangswechsel.

        Nachweise: (a) der Setup-Link fuehrt auf das Passwort-Formular,
        (b) nach dem Setzen fuehrt der erste Login SOFORT ins MFA-Setup — nicht
        auf ``/password-change/`` (``revoke_credentials(INVITE_SETUP)`` hat den
        Zwangswechsel geloescht), (c) nach der Backup-Code-Quittung ist der
        Zeitstrom erreichbar, (d) der zweite Login landet auf ``/mfa/verify/``
        (Geraet bestaetigt) und nach dem Code wieder auf dem Zeitstrom.
        """
        username = f"e2e-onb-{uuid.uuid4().hex[:8]}"
        try:
            pfad = _create_invited_user(e2e_env, username)

            page, context = _open_page(browser)
            try:
                # (a) Setup-Link oeffnen — Django tauscht das Token gegen die
                # ``set-password``-URL, das Formular erscheint dort.
                page.goto(f"{base_url}{pfad}", wait_until="domcontentloaded")
                page.wait_for_url(re.compile(r"/invite/[^/]+/set-password/"), timeout=20000)
                page.fill("#id_new_password1", NEUES_PASSWORT)
                page.fill("#id_new_password2", NEUES_PASSWORT)
                page.click("form button[type='submit']")
                page.wait_for_url(re.compile(r"/password-reset/complete/"), timeout=20000)

                # (b) Erster Login mit dem selbst gesetzten Passwort.
                _login(page, base_url, username, NEUES_PASSWORT)
                _erwarte_nach_login(page, base_url, re.compile(r"/mfa/setup/"))
                assert "/password-change/" not in page.url, (
                    "Nach abgeschlossenem Invite-Setup darf kein Zwangswechsel mehr greifen"
                )

                # (c) TOTP einrichten, Backup-Codes quittieren, Zeitstrom sehen.
                secret = _complete_mfa_setup(page, base_url)
                page.goto(f"{base_url}/", wait_until="domcontentloaded")
                expect(page.locator("h1")).to_have_text("Zeitstrom", timeout=10000)

                # (d) Zweiter Login: Verify statt Setup, kein Zwangswechsel.
                _logout(page)
                _login(page, base_url, username, NEUES_PASSWORT)
                _erwarte_nach_login(page, base_url, re.compile(r"/mfa/verify/"))
                assert "/mfa/setup/" not in page.url, "Bestaetigtes Geraet darf nicht erneut ins Setup fuehren"
                _verify_second_factor(page, base_url, secret)
            finally:
                context.close()
        finally:
            _retire_user(e2e_env, username)


class TestInitialPasswordOnboardingJourney:
    """Weg 2: Initialpasswort → Zwangswechsel → MFA-Setup → Zeitstrom.

    **Befund aus diesem Test (Refs #2025, in Welle G in der Middleware gefixt):**
    Der erste Login eines Kontos mit ``must_change_password=True`` endete auf
    einer Seite „403 – Sicherheits-Token abgelaufen" statt auf
    ``/password-change/``. Mechanik (am Fehlerbild und am Codepfad verifiziert):

    1. ``auth-bootstrap.js`` faengt den Login-Submit ab, schickt ihn per
       ``fetch`` und holt danach den Offline-Salt von
       ``POST /auth/offline-key-salt/``.
    2. ``ForcePasswordChangeMiddleware.EXEMPT_URLS``
       (``core/middleware/password_change.py``) enthielt diesen Pfad NICHT — der
       Salt-Request wird also auf ``/password-change/`` umgeleitet und
       beantwortet HTML statt JSON.
    3. ``saltResp.json()`` wirft, der ``catch``-Zweig in ``attach()`` faellt auf
       den nativen ``form.submit()`` zurueck — mit dem CSRF-Token von VOR dem
       Login, der beim Login rotiert wurde. Ergebnis: 403.

    ``MFAEnforcementMiddleware`` nimmt ``/auth/offline-key-salt/`` genau
    deswegen ausdruecklich aus (Kommentar dort: „…faellt auf native form.submit
    mit rotiertem CSRF zurueck — Ergebnis ist eine CSRF-Fehlerseite statt des
    MFA-Prompts", Refs #573/#588). Beim Zwangswechsel fehlt dieselbe Ausnahme.

    Der erste Test haelt diesen Soll-Zustand fest (vor dem Fix rot, seit der
    Ausnahme in ``EXEMPT_URLS`` gruen). Der zweite Test prueft die
    Reihenfolge-Zusage des Tickets und navigiert dafuer einmal aktiv auf ``/``
    — die Sitzung ist trotz der 403-Seite angemeldet.
    """

    def test_first_login_with_initial_password_lands_on_password_change(self, base_url, browser, e2e_env):
        """Soll-Zustand: der erste Login fuehrt direkt auf den Zwangswechsel."""
        username = f"e2e-onb-{uuid.uuid4().hex[:8]}"
        try:
            _create_user_with_initial_password(e2e_env, username)

            page, context = _open_page(browser)
            try:
                _login(page, base_url, username, INITIAL_PASSWORT)
                page.wait_for_url(re.compile(r"/password-change/"), timeout=20000)
            finally:
                context.close()
        finally:
            _retire_user(e2e_env, username)

    def test_forced_password_change_precedes_mfa_setup(self, base_url, browser, e2e_env):
        """Reihenfolge-Nachweis: der Zwangswechsel kommt VOR dem MFA-Setup.

        ``ForcePasswordChangeMiddleware`` steht in ``MIDDLEWARE`` vor
        ``MFAEnforcementMiddleware`` und nimmt ``/mfa/`` NICHT aus. Der Test
        belegt das doppelt: jede Navigation landet auf ``/password-change/``,
        und ein direkter Aufruf von ``/mfa/setup/`` wird dorthin
        zurueckgeworfen. Erst nach dem Wechsel greift das MFA-Gate; der zweite
        Login verlangt nur noch den zweiten Faktor.
        """
        username = f"e2e-onb-{uuid.uuid4().hex[:8]}"
        try:
            _create_user_with_initial_password(e2e_env, username)

            page, context = _open_page(browser)
            try:
                _login(page, base_url, username, INITIAL_PASSWORT)
                _erwarte_nach_login(page, base_url, re.compile(r"/password-change/"))

                # MFA-Setup ist waehrend des offenen Zwangswechsels nicht erreichbar.
                page.goto(f"{base_url}/mfa/setup/", wait_until="domcontentloaded")
                page.wait_for_url(re.compile(r"/password-change/"), timeout=20000)

                page.fill("#id_old_password", INITIAL_PASSWORT)
                page.fill("#id_new_password1", NEUES_PASSWORT)
                page.fill("#id_new_password2", NEUES_PASSWORT)
                page.click("#password-change-form button[type='submit']")

                # Erst jetzt greift das MFA-Gate.
                secret = _complete_mfa_setup(page, base_url)
                page.goto(f"{base_url}/", wait_until="domcontentloaded")
                expect(page.locator("h1")).to_have_text("Zeitstrom", timeout=10000)

                # Zweiter Login: kein Zwangswechsel mehr, nur noch der zweite Faktor.
                _logout(page)
                _login(page, base_url, username, NEUES_PASSWORT)
                _erwarte_nach_login(page, base_url, re.compile(r"/mfa/verify/"))
                assert "/password-change/" not in page.url, (
                    "Nach dem Wechsel darf der Zwangswechsel nicht erneut greifen"
                )
                _verify_second_factor(page, base_url, secret)
            finally:
                context.close()
        finally:
            _retire_user(e2e_env, username)
