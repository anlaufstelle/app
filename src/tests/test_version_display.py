"""Refs #1050: Deployte Version im UI (Footer eingeloggt, nicht Login).

SemVer kommt aus ``pyproject.toml`` (via ``app_versions()``), der
Build-Zusatz aus dem ENV ``APP_VERSION``, das die Image-Builds einbacken
(Dev-Image: ``main-<sha>``, Release: ``v<semver>``). Auf Releases ist der
Build-Zusatz redundant zum SemVer und wird unterdrückt — sichtbar bleibt
er nur auf dev. Login-Seite zeigt bewusst keine Version (Fingerprinting).
"""

import pytest
from django.urls import reverse

from core import context_processors


@pytest.fixture(autouse=True)
def _clear_version_cache():
    context_processors._app_version_info.cache_clear()
    yield
    context_processors._app_version_info.cache_clear()


@pytest.mark.django_db
class TestVersionFooter:
    def test_footer_shows_semver_for_authenticated(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        content = response.content.decode()
        assert 'data-testid="app-version"' in content

    def test_authenticated_footer_shows_source_link_and_only_app_version(self, client, staff_user, settings):
        settings.SOURCE_CODE_VERSION = "abcdef1234567890"
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        content = response.content.decode()

        assert "Quellcode" in content
        assert content.count("abcdef1") == 0
        assert content.count('data-testid="app-version"') == 1

    def test_login_page_shows_no_version(self, client):
        response = client.get(reverse("login"))
        assert 'data-testid="app-version"' not in response.content.decode()

    def test_login_page_shows_no_source_code_version(self, client, settings):
        """Refs #1901: auch bei gesetzter SOURCE_CODE_VERSION bleibt die
        anonyme Login-Seite versionslos (Fingerprinting) -- die Luecke, durch
        die der Versions-Span als Nebenwirkung von #1812 sichtbar wurde."""
        settings.SOURCE_CODE_VERSION = "abcdef1234567890"
        response = client.get(reverse("login"))
        assert b"abcdef1" not in response.content


class TestAppVersionInfo:
    def test_dev_build_suffix_is_exposed(self, monkeypatch):
        monkeypatch.setenv("APP_VERSION", "main-0123456789abcdef")
        semver, build = context_processors._app_version_info()
        assert semver  # SemVer aus pyproject.toml
        assert build == "main-0123456789abcdef"

    def test_release_build_suffix_is_suppressed(self, monkeypatch):
        semver_probe, _ = context_processors._app_version_info()
        context_processors._app_version_info.cache_clear()
        monkeypatch.setenv("APP_VERSION", f"v{semver_probe}")
        semver, build = context_processors._app_version_info()
        assert semver == semver_probe
        assert build == ""

    def test_missing_env_means_no_suffix(self, monkeypatch):
        monkeypatch.delenv("APP_VERSION", raising=False)
        _semver, build = context_processors._app_version_info()
        assert build == ""

    def test_fallback_env_version_strips_leading_v(self, monkeypatch):
        """Refs #1504: Container-Deploys ohne lesbares ``pyproject.toml``
        fallen in ``app_versions()`` auf den ``APP_VERSION``-Env-Wert
        zurueck, der per ADR-028 das fuehrende 'v' traegt (z.B.
        ``v0.20.0``). ``base.html`` prependet im Footer immer genau ein
        'v' -- ohne Normalisierung ergaebe das 'vv0.20.0'."""
        monkeypatch.setattr(
            "core.services.system.health.app_versions",
            lambda: {"app_version": "v0.20.0"},
        )
        semver, _build = context_processors._app_version_info()
        assert semver == "0.20.0"

    def test_local_pyproject_version_has_no_leading_v(self):
        """Gegen-Check: der lokale Lesepfad (pyproject.toml vorhanden,
        Version ohne 'v') bleibt unveraendert korrekt -- kein
        Doppel-Strip o.ae."""
        semver, _build = context_processors._app_version_info()
        assert semver and not semver.startswith("v")


@pytest.mark.django_db
class TestVersionFooterExactRendering:
    def test_footer_renders_exactly_v_prefix_once_from_env_fallback(self, client, staff_user, monkeypatch):
        """Refs #1504: End-to-End-Beweis fuer den Footer-Bug -- Fallback
        auf den 'v'-praefixierten Env-Wert darf im gerenderten Footer
        nicht zu 'vv0.20.0' fuehren."""
        monkeypatch.setattr(
            "core.services.system.health.app_versions",
            lambda: {"app_version": "v0.20.0"},
        )
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        content = response.content.decode()
        assert 'data-testid="app-version">v0.20.0<' in content
        assert "vv0.20.0" not in content


@pytest.mark.django_db
class TestPublicFooterRendering:
    """Refs #2102: der gemeinsame Fussbaustein auf allen Standalone-Seiten.

    Zwei Zusagen in einem Test-Satz:

    * **AGPL §13** (Refs #835): der Quellcode-Link steht auf JEDER
      ausgelieferten Seite mit Footer — bis #2102 fehlte er auf zehn
      Standalone-Seiten komplett und auf den vier Passwort-Reset-Seiten.
    * **Kein Versions-Leak** (Refs #1901/#1050): weder ``app_version`` noch
      ``SOURCE_CODE_VERSION`` noch der Hilfe-Link erscheinen auf einer nicht
      angemeldeten Seite — und auch nicht auf ``mfa_verify``/
      ``password_change``, wo die Sitzung zwar eingeloggt, der 2FA-Nachweis
      aber noch nicht erbracht bzw. das Passwort noch nicht gewechselt ist.
    """

    # Refs #2102 (Cross-Review M-3): die Zusage lautet "auf ALLEN Footer-Seiten",
    # also werden auch alle 17 Dokumente gerendert geprueft — nicht nur die
    # bequem erreichbaren zehn. Die Zuordnung Route -> Template ist explizit,
    # damit ``test_every_footer_document_is_covered`` sie gegen den echten
    # Template-Scan halten kann: verschwindet ein Dokument oder kommt eins dazu,
    # faellt die Luecke hier auf und nicht erst im Handtest.
    _ANONYMOUS_ROUTES = {
        "login": "auth/login.html",
        "password_reset": "registration/password_reset_form.html",
        "password_reset_done": "registration/password_reset_done.html",
        "password_reset_complete": "registration/password_reset_complete.html",
        "core:lockout_recovery_request": "auth/lockout_recovery_request.html",
        "core:lockout_recovery_sent": "auth/lockout_recovery_sent.html",
        "core:lockout_recovery_backup_code": "auth/lockout_recovery_backup_code.html",
        # Precachte Offline-Shell — erbt von offline_base.html.
        "core:offline_client_list_shell": "offline_base.html",
    }
    # Eingeloggt, aber vor dem 2FA-Nachweis bzw. vor dem erzwungenen
    # Passwortwechsel: hier darf trotz Anmeldung keine Version stehen.
    _GATED_ROUTES = {
        "mfa_verify": "auth/mfa_login.html",
        "mfa_setup": "auth/mfa_setup.html",
        "password_change": "auth/password_change.html",
    }
    # Vollstaendig angemeldet — der Gegen-Check, dass base.html den Footer behaelt.
    _AUTHENTICATED_ROUTES = {"core:zeitstrom": "base.html"}
    # Keine anonym erreichbare URL: Token-Seiten und die beiden
    # Middleware-Sperrseiten werden wie im Favicon-Test direkt gerendert.
    _RENDERED_TEMPLATES = (
        "auth/facility_missing.html",
        "auth/facility_archived.html",
        "auth/lockout_recovery_confirm.html",
        "auth/lockout_recovery_invalid.html",
        "registration/password_reset_confirm.html",
        # Notice for account links opened in a signed-in session (Refs #2391);
        # also reached before the second factor, so it never shows a version.
        "auth/signed_in_link_notice.html",
    )
    # Anonym erreichbar, existiert aber nur bei ``DEMO_MODE`` (Refs #2285):
    # eigener Test mit eingeschaltetem Demo-Modus.
    _DEMO_ROUTES = {"demo_scenario_select": "auth/demo_scenario_select.html"}

    def test_every_footer_document_is_covered(self):
        """Vollstaendigkeits-Assert gegen den Architektur-Scanner.

        Quelle der Wahrheit ist derselbe Scan, den
        ``TestStandaloneDocumentGuard`` fuer die Include-Pflicht fuehrt — eine
        hier von Hand gepflegte Liste koennte sonst leise hinter dem
        Template-Baum zurueckbleiben (genau die Luecke aus dem Cross-Review).
        """
        from tests.test_architecture_guards_templates import TestStandaloneDocumentGuard as Guard

        expected = {rel for rel, _source in Guard._standalone_documents() if rel not in Guard._FOOTERLESS_TEMPLATES}
        covered = (
            set(self._ANONYMOUS_ROUTES.values())
            | set(self._GATED_ROUTES.values())
            | set(self._AUTHENTICATED_ROUTES.values())
            | set(self._RENDERED_TEMPLATES)
            | set(self._DEMO_ROUTES.values())
        )
        assert covered == expected, (
            "Footer-Renderingtests und Template-Inventar sind auseinandergelaufen.\n"
            f"Ungeprueft: {sorted(expected - covered)}\n"
            f"Nicht (mehr) vorhanden: {sorted(covered - expected)}"
        )

    @staticmethod
    def _assert_footer_without_version(body, label):
        assert "/blob/main/LICENSE" in body, f"{label}: AGPL-3.0-Lizenzlink fehlt (Refs #835)"
        assert "Quellcode" in body, f"{label}: AGPL-§13-Quellcode-Link fehlt (Refs #835)"
        assert 'data-testid="app-version"' not in body, f"{label}: Versionsanzeige leakt (Refs #1050/#1901)"
        assert 'data-testid="footer-help"' not in body, f"{label}: Hilfe-Link nur fuer Angemeldete"

    @pytest.mark.parametrize("url_name", sorted(_ANONYMOUS_ROUTES))
    def test_anonymous_pages_show_source_link_but_no_version(self, client, settings, url_name):
        settings.SOURCE_CODE_VERSION = "abcdef1234567890"
        response = client.get(reverse(url_name))
        assert response.status_code == 200
        body = response.content.decode()
        self._assert_footer_without_version(body, url_name)
        assert "abcdef1" not in body, f"{url_name}: SOURCE_CODE_VERSION leakt anonym (Refs #1901)"

    @pytest.mark.django_db
    @pytest.mark.parametrize("url_name", sorted(_DEMO_ROUTES))
    def test_demo_pages_show_source_link_but_no_version(self, client, settings, url_name):
        """Refs #2285: die Demo-Bereichsauswahl ist anonym, aber nur bei ``DEMO_MODE`` vorhanden."""
        settings.DEMO_MODE = True
        settings.SOURCE_CODE_VERSION = "abcdef1234567890"
        response = client.get(reverse(url_name))
        assert response.status_code == 200
        body = response.content.decode()
        self._assert_footer_without_version(body, url_name)
        assert "abcdef1" not in body, f"{url_name}: SOURCE_CODE_VERSION leakt anonym (Refs #1901)"

    def test_mfa_verify_shows_source_link_but_no_version(self, client, settings, staff_user):
        """Eingeloggt, aber ohne 2FA-Nachweis — kein Versions-Leak (Refs #1901)."""
        from django_otp.plugins.otp_totp.models import TOTPDevice

        settings.SOURCE_CODE_VERSION = "abcdef1234567890"
        TOTPDevice.objects.create(user=staff_user, name="test", confirmed=True)
        client.force_login(staff_user)
        response = client.get(reverse("mfa_verify"))
        assert response.status_code == 200
        body = response.content.decode()
        self._assert_footer_without_version(body, "mfa_verify")
        assert "abcdef1" not in body, "mfa_verify: SOURCE_CODE_VERSION leakt vor dem 2FA-Nachweis (Refs #1901)"

    @pytest.mark.parametrize("url_name", ["mfa_setup", "password_change"])  # mfa_verify: eigener Test (TOTP-Setup)
    def test_other_gated_pages_show_source_link_but_no_version(self, client, settings, staff_user, url_name):
        settings.SOURCE_CODE_VERSION = "abcdef1234567890"
        client.force_login(staff_user)
        response = client.get(reverse(url_name))
        assert response.status_code == 200
        body = response.content.decode()
        self._assert_footer_without_version(body, url_name)
        assert "abcdef1" not in body, f"{url_name}: SOURCE_CODE_VERSION leakt (Refs #1901)"

    @pytest.mark.parametrize("template", _RENDERED_TEMPLATES)
    def test_directly_rendered_pages_show_source_link_but_no_version(self, settings, template):
        """Token-Seiten und Middleware-Sperrseiten haben keine anonym
        erreichbare URL — sie werden mit demselben Minimalkontext gerendert,
        den ``middleware/facility_scope.py`` baut (Refs #2090/#2102).

        ``SOURCE_CODE_VERSION`` liegt dabei ausdruecklich IM Kontext: der Test
        beweist damit, dass der Baustein sie ohne ``footer_authenticated``
        nicht ausgibt — und nicht bloss, dass sie fehlt.
        """
        from django.template.loader import render_to_string

        from core import context_processors

        settings.SOURCE_CODE_VERSION = "abcdef1234567890"
        context = {
            "csrf_token": "x",
            "page_language": "de",
            # lockout_recovery_confirm.html baut daraus seine Formular-Action.
            "token": "pruefsonde",
            **context_processors.source_code(None),
            **context_processors.legal_pages(None),
        }
        assert context["SOURCE_CODE_VERSION"] == "abcdef1234567890", "Kontext-Sonde: Version muss im Kontext liegen."
        body = render_to_string(template, context)
        self._assert_footer_without_version(body, template)
        assert "abcdef1" not in body, f"{template}: SOURCE_CODE_VERSION leakt (Refs #1901)"

    def test_authenticated_app_page_keeps_version_and_help(self, client, settings, staff_user):
        """Gegen-Check: der Umbau darf den Footer aus ``base.html`` nicht entkernen."""
        settings.SOURCE_CODE_VERSION = "abcdef1234567890"
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        body = response.content.decode()
        assert "Quellcode" in body
        assert 'data-testid="app-version"' in body
        assert 'data-testid="footer-help"' in body
        assert "abcdef1" not in body


@pytest.mark.django_db
class TestFooterVersionGateForPartiallyAuthenticatedSessions:
    """Refs #2286: die Version haengt am Prädikat „Sitzung vollstaendig
    angemeldet", nicht mehr an ``footer_authenticated and user.is_authenticated``.

    Die Zustaende der Befundtabelle des Tickets:

    * **B** Passwort geprueft, bestaetigtes Geraet, ``mfa_verified`` fehlt,
    * **C** 2FA bewusst erzwungen (``mfa_required``), noch kein Geraet,
    * **D** erzwungener Passwortwechsel.

    Alle drei erreichen Seiten, die von ``base.html`` erben (404 und die
    403-CSRF-Seite unter den von der MFA-Middleware ausgenommenen Praefixen,
    in **C** zusaetzlich ``/mfa/settings/`` und ``/mfa/backup-codes/`` im
    laufenden Setup). Dort darf weder ``app_version`` noch
    ``SOURCE_CODE_VERSION`` noch der Hilfe-Link stehen.
    """

    SENTINEL = "abcdef1234567890"

    @staticmethod
    def _assert_no_version(body, label, sentinel):
        assert "Quellcode" in body, f"{label}: AGPL-§13-Quellcode-Link fehlt (Refs #835)"
        assert 'data-testid="app-version"' not in body, f"{label}: Versionsanzeige leakt (Refs #2286)"
        assert 'data-testid="footer-help"' not in body, f"{label}: Hilfe-Link nur fuer vollstaendig Angemeldete"
        assert sentinel[:7] not in body, f"{label}: SOURCE_CODE_VERSION leakt (Refs #2286)"

    # --- Zustand B: zweiter Faktor offen -------------------------------------
    @staticmethod
    def _state_b(client, user):
        from django_otp.plugins.otp_totp.models import TOTPDevice

        TOTPDevice.objects.create(user=user, name="test", confirmed=True)
        client.force_login(user)

    def test_state_b_404_under_exempt_prefix_has_no_version(self, client, settings, staff_user):
        settings.SOURCE_CODE_VERSION = self.SENTINEL
        self._state_b(client, staff_user)
        response = client.get("/mfa/gibt-es-nicht/")
        assert response.status_code == 404
        self._assert_no_version(response.content.decode(), "B/404", self.SENTINEL)

    def test_state_b_csrf_failure_page_has_no_version(self, settings, staff_user):
        from django.test import Client

        settings.SOURCE_CODE_VERSION = self.SENTINEL
        csrf_client = Client(enforce_csrf_checks=True)
        self._state_b(csrf_client, staff_user)
        response = csrf_client.post(reverse("mfa_verify"), {"token": "000000"})
        assert response.status_code == 403
        self._assert_no_version(response.content.decode(), "B/403-CSRF", self.SENTINEL)

    # --- Zustand C: 2FA erzwungen, noch kein Geraet --------------------------
    @staticmethod
    def _state_c(client, user):
        user.mfa_required = True
        user.save(update_fields=["mfa_required"])
        client.force_login(user)

    def test_state_c_mfa_settings_page_has_no_version(self, client, settings, staff_user):
        settings.SOURCE_CODE_VERSION = self.SENTINEL
        self._state_c(client, staff_user)
        response = client.get(reverse("mfa_settings"))
        assert response.status_code == 200
        self._assert_no_version(response.content.decode(), "C//mfa/settings/", self.SENTINEL)

    def test_state_c_backup_codes_during_setup_has_no_version(self, client, settings, staff_user):
        settings.SOURCE_CODE_VERSION = self.SENTINEL
        self._state_c(client, staff_user)
        session = client.session
        session["mfa_backup_codes"] = ["code-eins", "code-zwei"]
        session["mfa_setup_pending"] = True
        session.save()
        response = client.get(reverse("mfa_backup_codes"))
        assert response.status_code == 200
        self._assert_no_version(response.content.decode(), "C//mfa/backup-codes/", self.SENTINEL)

    def test_state_c_404_under_exempt_prefix_has_no_version(self, client, settings, staff_user):
        settings.SOURCE_CODE_VERSION = self.SENTINEL
        self._state_c(client, staff_user)
        response = client.get("/mfa/gibt-es-nicht/")
        assert response.status_code == 404
        self._assert_no_version(response.content.decode(), "C/404", self.SENTINEL)

    # --- Zustand D: erzwungener Passwortwechsel ------------------------------
    @staticmethod
    def _state_d(client, user):
        user.must_change_password = True
        user.save(update_fields=["must_change_password"])
        client.force_login(user)

    def test_state_d_404_under_exempt_prefix_has_no_version(self, client, settings, staff_user):
        settings.SOURCE_CODE_VERSION = self.SENTINEL
        self._state_d(client, staff_user)
        response = client.get("/login/gibt-es-nicht/")
        assert response.status_code == 404
        self._assert_no_version(response.content.decode(), "D/404", self.SENTINEL)

    def test_state_d_csrf_failure_page_has_no_version(self, settings, staff_user):
        from django.test import Client

        settings.SOURCE_CODE_VERSION = self.SENTINEL
        csrf_client = Client(enforce_csrf_checks=True)
        self._state_d(csrf_client, staff_user)
        response = csrf_client.post(reverse("password_change"), {"old_password": "x"})
        assert response.status_code == 403
        self._assert_no_version(response.content.decode(), "D/403-CSRF", self.SENTINEL)

    # --- Gegen-Check: vollstaendig angemeldet behaelt alles -------------------
    def test_fully_authenticated_404_keeps_version(self, client, settings, staff_user):
        """Der Fix darf die Fehlerseite einer vollstaendig angemeldeten
        Sitzung nicht entkernen — sonst waere die Zusage trivial erfuellt."""
        settings.SOURCE_CODE_VERSION = self.SENTINEL
        client.force_login(staff_user)
        response = client.get("/gibt-es-nicht/")
        assert response.status_code == 404
        body = response.content.decode()
        assert 'data-testid="app-version"' in body
        assert self.SENTINEL[:7] not in body
