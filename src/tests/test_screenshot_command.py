"""Static config checks for the ``screenshot`` doc-generation command.

Playwright/PIL are only imported lazily inside ``Command.handle()`` (see
``core/management/commands/screenshot.py``), so the module itself — and its
``SHOTS`` config — can be imported and asserted on without a live E2E server.

Refs #1594, #1595, #2325.
"""

import io
import uuid

import pytest
from django.core.management.base import CommandError
from django.utils import timezone

from core.management.commands.screenshot import DESKTOP, LANG_SWITCH_USERNAMES, SHOTS, Command, Shot
from core.models import Case, Client, User


def _shot(name: str):
    return next(s for s in SHOTS if s.name == name)


class TestDsgvoPaketRole:
    """Refs #1595: dsgvo-paket sitzt hinter ``FacilityAdminRequiredMixin`` —
    der Default-Seed-User "emma" ist nur LEAD und bekommt 403.
    """

    def test_dsgvo_paket_shot_uses_facility_admin_role(self):
        assert _shot("dsgvo-paket").role == "admin"

    def test_lang_switch_usernames_include_admin(self):
        """Sonst rendert der EN-Durchlauf fuer den admin-Login deutsch."""
        assert "admin" in LANG_SWITCH_USERNAMES


class TestBeratungsstelleGalerieShot:
    """Refs #2148 (Maintainer-Entscheid 2026-09-06): einziger Shot, der den
    Paket-Unterschied der Zweigstelle Nord zeigt (Typwahl Kurzberatung/
    Beratungsgespraech/Krisengespraech/Begleitung statt Kontaktladen-Typen).
    """

    def test_shot_uses_emma_1_role_and_is_not_mobile(self):
        shot = _shot("ereignis-anlegen-beratungsstelle")
        assert shot.route == "core:event_create"
        assert shot.role == "emma_1"
        assert shot.mobile is False

    def test_shot_renders_in_both_languages(self):
        """Maintainer-Entscheid: DE + EN (NIEDRIG B) — explizit
        gepinnt statt nur ueber den Dataclass-Default abgedeckt."""
        assert _shot("ereignis-anlegen-beratungsstelle").langs == ("de", "en")

    def test_shot_sits_directly_after_the_standard_event_shot(self):
        """Maintainer-Entscheid 2026-09-06: Position direkt nach dem
        Standard-Shot ``ereignis-anlegen`` in der Galerie (NIEDRIG B)."""
        names = [s.name for s in SHOTS]
        idx_standard = names.index("ereignis-anlegen")
        idx_beratungsstelle = names.index("ereignis-anlegen-beratungsstelle")
        assert idx_beratungsstelle == idx_standard + 1

    def test_lang_switch_usernames_include_emma_1(self):
        """Sonst rendert der EN-Durchlauf fuer den emma_1-Login deutsch."""
        assert "emma_1" in LANG_SWITCH_USERNAMES


class TestStatistikenWaitJs:
    """Refs #1594: die Statistik-Seite zeichnet ihre Charts clientseitig
    asynchron — der Shot muss auf das ``data-charts-ready``-Attribut warten,
    nicht nur auf den (sofort vorhandenen, aber leeren) Canvas-Selektor.
    """

    def test_statistiken_shot_waits_for_charts_ready_marker(self):
        shot = _shot("statistiken")
        assert shot.wait_js is not None
        assert "data-charts-ready" in shot.wait_js

    def test_statistik_extern_shot_unaffected(self):
        """external_report.html hat keine Charts — kein wait_js noetig."""
        assert _shot("statistik-extern").wait_js is None


class TestNeueFlaechenV081:
    """Refs #1843/#1900: die vier mit #1803/#1813 entstandenen Flaechen —
    Bereichs-Uebersichten, Statistik-Reiterzeile (Datensatz-Berichte-Reiter)
    und die ``/search/``-Vollseite — gehoeren in die Doku-Screenshots.
    """

    def test_hub_shots_exist_with_admin_role(self):
        """Beide Hubs zeigen Verwaltungs-Statuslisten; ``/verwaltung/`` sitzt
        hinter ``FacilityAdminRequiredMixin`` — wie beim dsgvo-paket-Shot
        braucht es den admin-Login (emma ist nur LEAD).
        """
        assert _shot("datenschutz-uebersicht").role == "admin"
        assert _shot("verwaltung-uebersicht").role == "admin"

    def test_statistik_berichte_shot_uses_german_tab_param(self):
        """Der Datensatz-Berichte-Reiter zeigt Reiterzeile + Berichtskarten;
        die Adresse ist seit #1804 deutsch (``?ansicht=datensaetze``).
        """
        assert _shot("statistik-berichte").route == "/statistics/?ansicht=datensaetze"

    def test_suche_shot_resolves_query_from_seed_client(self):
        """Der Bulk-Seed wuerfelt Pseudonyme — eine hartkodierte Query faende
        je nach Lauf nichts. Der Shot traegt deshalb einen Platzhalter, den
        ``_url`` aus dem ersten Seed-Client aufloest (Wortteil, damit die
        Ergebnisseite mehrere Treffer zeigt).
        """
        shot = _shot("suche")
        assert "{client_pseudonym}" in shot.route
        assert shot.full_page is True

    def test_url_substitutes_client_pseudonym_placeholder(self):
        from core.management.commands.screenshot import Command

        class _FakeClient:
            pseudonym = "Fuchs-12"

        cmd = Command()
        shot = _shot("suche")
        url = cmd._url("http://x", shot, {"client": _FakeClient()})
        assert url == "http://x/search/?q=Fuchs"

    def test_url_returns_none_without_client(self):
        """Kein Seed-Client → Shot wird uebersprungen statt eine leere
        Ergebnisseite zu dokumentieren (Muster der pk_kind-Aufloesung).
        """
        from core.management.commands.screenshot import Command

        cmd = Command()
        assert cmd._url("http://x", _shot("suche"), {"client": None}) is None


@pytest.mark.django_db
class TestResolvePksMirrorsDetailViewLiveFilters:
    """Refs #2325: ``_resolve_pks()`` used to pick the lowest-pk case/client
    per facility with no further filter, while ``CaseDetailView`` only
    renders cases from ``cases_with_live_client()`` and ``ClientDetailView``
    refuses a trashed (but not an anonymized) person. A trashed/anonymized
    seed row at the lowest UUID made ``make docs-screens`` photograph a 404
    page for ``fallakte``/``fallakte-en`` (and exit 0 regardless, see the
    other test classes below).
    """

    def test_skips_a_trashed_case_at_the_lowest_pk(self, facility):
        emma = User.objects.create_user(username="emma", role=User.Role.LEAD, facility=facility)
        client = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Live-01",
            created_by=emma,
        )
        trashed_case = Case.objects.create(
            id=uuid.UUID(int=1),
            facility=facility,
            client=client,
            title="Trashed case",
            status=Case.Status.OPEN,
            created_by=emma,
            is_deleted=True,
            deleted_at=timezone.now(),
        )
        live_case = Case.objects.create(
            id=uuid.UUID(int=2),
            facility=facility,
            client=client,
            title="Live case",
            status=Case.Status.OPEN,
            created_by=emma,
        )
        assert trashed_case.pk < live_case.pk  # sanity: the trashed row sorts first by pk

        pks = Command()._resolve_pks()

        assert pks["case"] == live_case

    def test_skips_the_case_of_an_anonymized_client_at_the_lowest_pk(self, facility):
        """``cases_with_live_client()`` also excludes a case whose person was
        anonymized (``is_active=False``), not only a trashed case (Refs
        #1777) — the seed carries exactly such a case ahead of a live one.
        """
        emma = User.objects.create_user(username="emma", role=User.Role.LEAD, facility=facility)
        anonymized_client = Client.objects.create(
            id=uuid.UUID(int=1),
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Anon-01",
            created_by=emma,
            is_active=False,
        )
        live_client = Client.objects.create(
            id=uuid.UUID(int=2),
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Live-01",
            created_by=emma,
        )
        case_of_anonymized_client = Case.objects.create(
            id=uuid.UUID(int=1),
            facility=facility,
            client=anonymized_client,
            title="Case of an anonymized client",
            status=Case.Status.OPEN,
            created_by=emma,
        )
        live_case = Case.objects.create(
            id=uuid.UUID(int=2),
            facility=facility,
            client=live_client,
            title="Live case",
            status=Case.Status.OPEN,
            created_by=emma,
        )
        assert case_of_anonymized_client.pk < live_case.pk

        pks = Command()._resolve_pks()

        assert pks["case"] == live_case

    def test_skips_a_trashed_client_at_the_lowest_pk(self, facility):
        emma = User.objects.create_user(username="emma", role=User.Role.LEAD, facility=facility)
        trashed_client = Client.objects.create(
            id=uuid.UUID(int=1),
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Trash-01",
            created_by=emma,
            is_deleted=True,
            deleted_at=timezone.now(),
        )
        live_client = Client.objects.create(
            id=uuid.UUID(int=2),
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Live-01",
            created_by=emma,
        )
        assert trashed_client.pk < live_client.pk

        pks = Command()._resolve_pks()

        assert pks["client"] == live_client

    def test_still_picks_an_anonymized_but_not_trashed_client(self, facility):
        """Boundary pin, not a regression: ``ClientDetailView`` renders an
        anonymized person directly (Refs #1777) — only the trash bin
        (``is_deleted``) is off-limits, so the fix must not filter on
        ``is_active`` for the client and turn every anonymized seed facility
        into a skipped ``personendetail`` shot. Passes both before and after
        the fix; included to pin the exact scope of the filter.
        """
        emma = User.objects.create_user(username="emma", role=User.Role.LEAD, facility=facility)
        anonymized_client = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Anon-01",
            created_by=emma,
            is_active=False,
        )

        pks = Command()._resolve_pks()

        assert pks["client"] == anonymized_client


def _tiny_png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (2, 2), color="white").save(buf, format="PNG")
    return buf.getvalue()


class _FakeResponse:
    """Stand-in for a Playwright ``Response`` (Refs #2325)."""

    def __init__(self, *, ok: bool, status: int):
        self.ok = ok
        self.status = status


class _FakeLocator:
    """Stand-in for a Playwright ``Locator`` — only ``click()`` is used."""

    def click(self):
        pass


class _FakePage:
    """Stand-in for a Playwright ``Page`` — no browser involved."""

    def __init__(self, response: "_FakeResponse"):
        self._response = response
        self.screenshot_called = False
        self.filled: list[tuple[str, str]] = []
        self.url = "about:blank"
        self.context: _FakeContext | None = None

    def goto(self, url, timeout=None):
        self.url = url
        return self._response

    def wait_for_load_state(self, *args, **kwargs):
        pass

    def fill(self, selector, value):
        self.filled.append((selector, value))

    def locator(self, selector):
        return _FakeLocator()

    def wait_for_url(self, predicate, timeout=None):
        # Pretend the login redirect already left ``/login/`` behind.
        self.url = "http://x/dashboard/"

    def wait_for_selector(self, *args, **kwargs):
        pass

    def wait_for_function(self, *args, **kwargs):
        pass

    def screenshot(self, **kwargs):
        self.screenshot_called = True
        return _tiny_png_bytes()


class _FakeContext:
    """Stand-in for a Playwright ``BrowserContext`` — tracks every page it
    creates and whether it was closed (Refs #2325 follow-up: contexts must be
    per-role, not shared, and must be closed at the end of the run).
    """

    def __init__(self, response: "_FakeResponse"):
        self._response = response
        self.pages: list[_FakePage] = []
        self.closed = False

    def new_page(self):
        page = _FakePage(self._response)
        page.context = self
        self.pages.append(page)
        return page

    def close(self):
        self.closed = True


class _FakeBrowser:
    """Stand-in for a Playwright ``Browser`` — counts ``new_context()`` calls
    so tests can assert on a context-per-role policy (Refs #2325 follow-up).
    """

    def __init__(self, response: "_FakeResponse | None" = None):
        self._response = response if response is not None else _FakeResponse(ok=True, status=200)
        self.contexts: list[_FakeContext] = []

    def new_context(self, **kwargs):
        context = _FakeContext(self._response)
        self.contexts.append(context)
        return context


class TestRunLangViewportAbortsOnHttpErrorPage:
    """Refs #2325: an error-status response must count as a failure instead
    of being screenshotted as if it were real content — no browser needed,
    ``_run_lang_viewport`` only calls the ``Page``/``Response`` surface used
    below.
    """

    def _anonymous_shot(self) -> Shot:
        return Shot(name="fake-shot", route="/fake/", role=None, langs=("de",))

    def test_counts_a_404_response_as_a_failure_without_writing_an_image(self, tmp_path):
        browser = _FakeBrowser(_FakeResponse(ok=False, status=404))
        cmd = Command()

        ok, fail = cmd._run_lang_viewport(
            browser, {}, "http://x", tmp_path, "de", False, [self._anonymous_shot()], {}, 82
        )

        assert (ok, fail) == (0, 1)
        page = browser.contexts[0].pages[0]
        assert page.screenshot_called is False
        assert list(tmp_path.iterdir()) == []

    def test_counts_a_200_response_as_success_and_writes_an_image(self, tmp_path):
        browser = _FakeBrowser(_FakeResponse(ok=True, status=200))
        cmd = Command()

        ok, fail = cmd._run_lang_viewport(
            browser, {}, "http://x", tmp_path, "de", False, [self._anonymous_shot()], {}, 82
        )

        assert (ok, fail) == (1, 0)
        page = browser.contexts[0].pages[0]
        assert page.screenshot_called is True
        assert len(list(tmp_path.iterdir())) == 1


class TestPageForGivesEachRoleItsOwnContext:
    """Refs #2325 (follow-up): ``page_for()`` used to call ``context.new_page()``
    on one context shared by every role within a language/viewport run. Pages
    on the same context share cookies, so a second role's login silently
    replaced the first role's session cookie: the docs-screens shot right
    after ``ereignis-anlegen-beratungsstelle`` (role ``emma_1``) rendered the
    next shot, ``fallakte`` (role ``emma``), under the Nord session and
    404'd.
    """

    @staticmethod
    def _role_shot(name: str, role: str) -> Shot:
        return Shot(name=name, route=f"/{name}/", role=role, langs=("de",))

    def test_two_different_roles_each_get_their_own_context(self, tmp_path):
        browser = _FakeBrowser()
        cmd = Command()
        shots = [self._role_shot("shot-a", "emma_1"), self._role_shot("shot-b", "emma")]

        ok, fail = cmd._run_lang_viewport(browser, {}, "http://x", tmp_path, "de", False, shots, {}, 82)

        assert (ok, fail) == (2, 0)
        assert len(browser.contexts) == 2
        emma_1_page = browser.contexts[0].pages[0]
        emma_page = browser.contexts[1].pages[0]
        assert emma_page.context is not emma_1_page.context
        assert all(context.closed for context in browser.contexts)

    def test_two_shots_of_the_same_role_share_one_context_and_log_in_once(self, tmp_path):
        browser = _FakeBrowser()
        cmd = Command()
        shots = [self._role_shot("shot-a", "emma"), self._role_shot("shot-b", "emma")]

        ok, fail = cmd._run_lang_viewport(browser, {}, "http://x", tmp_path, "de", False, shots, {}, 82)

        assert (ok, fail) == (2, 0)
        assert len(browser.contexts) == 1
        assert len(browser.contexts[0].pages) == 1
        # Username + password filled exactly once — the second shot reuses
        # the already-logged-in page instead of logging in again.
        assert len(browser.contexts[0].pages[0].filled) == 2


class _FlakyFakePage(_FakePage):
    """Stand-in whose ``goto`` raises a queued exception before eventually
    returning the response — Refs #2325 (follow-up): a verification run hit a
    transient ``net::ERR_ABORTED`` aborting navigation on a page reused from
    an earlier shot.
    """

    def __init__(self, response: "_FakeResponse", exceptions: list[Exception]):
        super().__init__(response)
        self._exceptions = list(exceptions)
        self.goto_calls = 0

    def goto(self, url, timeout=None):
        self.goto_calls += 1
        if self._exceptions:
            raise self._exceptions.pop(0)
        return super().goto(url, timeout=timeout)


class _OneShotContext:
    """Minimal ``BrowserContext`` stand-in that always hands back the same
    (flaky) page — enough for a single anonymous shot, no login involved."""

    def __init__(self, page):
        self._page = page
        self.closed = False

    def new_page(self):
        return self._page

    def close(self):
        self.closed = True


class _OneShotBrowser:
    """Minimal ``Browser`` stand-in pairing with ``_OneShotContext`` above."""

    def __init__(self, page):
        self._page = page

    def new_context(self, **kwargs):
        return _OneShotContext(self._page)


class TestRunLangViewportRetriesOnAbortedNavigation:
    """Refs #2325 (follow-up): a verification run of ``make docs-screens``
    against the fix from the first commit still ended red — not on an
    application error, but on ``Page.goto: net::ERR_ABORTED``, a Chromium
    navigation abort on a page reused across shots. One retry absorbs that
    transient abort; any other exception, or a second abort, must still fail
    the shot.
    """

    def _anonymous_shot(self) -> Shot:
        return Shot(name="fake-shot", route="/fake/", role=None, langs=("de",))

    def test_retries_once_after_an_aborted_navigation_and_then_succeeds(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.management.commands.screenshot.time.sleep", lambda *a, **k: None)
        page = _FlakyFakePage(
            _FakeResponse(ok=True, status=200),
            [Exception("Page.goto: net::ERR_ABORTED at http://x/fake/")],
        )
        browser = _OneShotBrowser(page)
        cmd = Command()

        ok, fail = cmd._run_lang_viewport(
            browser, {}, "http://x", tmp_path, "de", False, [self._anonymous_shot()], {}, 82
        )

        assert (ok, fail) == (1, 0)
        assert page.goto_calls == 2
        assert page.screenshot_called is True

    def test_fails_when_the_retried_navigation_also_aborts(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.management.commands.screenshot.time.sleep", lambda *a, **k: None)
        page = _FlakyFakePage(
            _FakeResponse(ok=True, status=200),
            [
                Exception("Page.goto: net::ERR_ABORTED at http://x/fake/"),
                Exception("Page.goto: net::ERR_ABORTED at http://x/fake/"),
            ],
        )
        browser = _OneShotBrowser(page)
        cmd = Command()

        ok, fail = cmd._run_lang_viewport(
            browser, {}, "http://x", tmp_path, "de", False, [self._anonymous_shot()], {}, 82
        )

        assert (ok, fail) == (0, 1)
        assert page.goto_calls == 2

    def test_does_not_retry_a_different_navigation_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.management.commands.screenshot.time.sleep", lambda *a, **k: None)
        page = _FlakyFakePage(
            _FakeResponse(ok=True, status=200),
            [Exception("Page.goto: net::ERR_CONNECTION_REFUSED at http://x/fake/")],
        )
        browser = _OneShotBrowser(page)
        cmd = Command()

        ok, fail = cmd._run_lang_viewport(
            browser, {}, "http://x", tmp_path, "de", False, [self._anonymous_shot()], {}, 82
        )

        assert (ok, fail) == (0, 1)
        assert page.goto_calls == 1


class _StubPlaywrightContextManager:
    """Minimal stand-in for ``sync_playwright()``'s context manager — enough
    surface for ``handle()`` to drive once ``_run_lang_viewport`` itself is
    monkeypatched below, so no real browser is launched.
    """

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    @property
    def chromium(self):
        return self

    def launch(self):
        return self

    def new_context(self, **kwargs):
        return self

    def close(self):
        pass


@pytest.mark.django_db
class TestHandleFailsTheRunWhenAnyShotFailed:
    """Refs #2325: ``handle()`` used to report "N fehlgeschlagen" and still
    exit 0 — ``make docs-screens`` turned a broken doc screenshot into a
    silent no-op instead of a red build.
    """

    def test_raises_command_error_when_a_shot_failed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: _StubPlaywrightContextManager())
        monkeypatch.setattr(Command, "_run_lang_viewport", lambda self, *a, **k: (0, 1))

        cmd = Command()
        with pytest.raises(CommandError, match="screenshot"):
            cmd.handle(base_url="http://x", output_dir=str(tmp_path), only="", quality=82)

    def test_does_not_raise_when_every_shot_succeeded(self, tmp_path, monkeypatch):
        monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: _StubPlaywrightContextManager())
        monkeypatch.setattr(Command, "_run_lang_viewport", lambda self, *a, **k: (1, 0))

        cmd = Command()
        cmd.handle(base_url="http://x", output_dir=str(tmp_path), only="", quality=82)


def _role_route_shot(name: str, role: str | None) -> Shot:
    """A literal-path shot that needs no reverse()/pk lookup, so it can run
    against a bare fake database with no seed data."""
    return Shot(name=name, route=f"/{name}/", role=role, langs=("de",))


# Refs #2325 (follow-up review): a module-wide log of every non-login
# navigation together with the ``sessionid`` cookie the page's own context
# carried at that moment. The earlier regression test for the per-role
# context fix asserted a *context contract* (two ``new_context()`` calls,
# different context identity) rather than observing an actual session
# leaking across roles, and it called ``_run_lang_viewport`` directly with
# the new nine-argument signature — that signature does not exist on the
# pre-fix command, so running it there raised ``TypeError`` instead of
# failing on the cookie mix-up it was meant to catch. ``handle()`` kept the
# same signature before and after the fix, so driving it end-to-end through
# a fake Playwright module is what actually exercises the old shared-context
# bug and fails for the right reason.
_NAVIGATION_LOG: list[tuple[str, str | None]] = []


class _FakeBrowserLauncher:
    """Minimal ``sync_playwright()`` stand-in handing back a pre-built fake
    browser instead of launching a real Chromium."""

    def __init__(self, browser):
        self._browser = browser

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    @property
    def chromium(self):
        return self

    def launch(self):
        return self._browser


class _SessionFakeLocator:
    """Stand-in for a Playwright ``Locator`` whose ``click()`` performs the
    fake login: it writes the username last filled on the page into that
    page's own context's cookie store, mirroring how a real login response
    sets the session cookie on the browser context that made the request."""

    def __init__(self, page: "_SessionFakePage"):
        self._page = page

    def click(self):
        self._page.context.cookies["sessionid"] = self._page.pending_username
        self._page.url = "http://x/dashboard/"


class _SessionFakePage:
    """Stand-in for a Playwright ``Page`` that knows its own
    ``BrowserContext`` and records every non-login navigation together with
    the ``sessionid`` cookie that context carries at that moment — the
    signal that exposes a session accidentally inherited from another role.
    """

    def __init__(self, context: "_SessionFakeContext"):
        self.context = context
        self.url = "about:blank"
        self.pending_username: str | None = None

    def goto(self, url, timeout=None):
        self.url = url
        if not url.endswith("/login/"):
            _NAVIGATION_LOG.append((url, self.context.cookies.get("sessionid")))
        return _FakeResponse(ok=True, status=200)

    def wait_for_load_state(self, *args, **kwargs):
        pass

    def fill(self, selector, value):
        if "username" in selector:
            self.pending_username = value

    def locator(self, selector):
        return _SessionFakeLocator(self)

    def wait_for_url(self, predicate, timeout=None):
        pass

    def wait_for_selector(self, *args, **kwargs):
        pass

    def wait_for_function(self, *args, **kwargs):
        pass

    def screenshot(self, **kwargs):
        return _tiny_png_bytes()


class _SessionFakeContext:
    """Stand-in for a Playwright ``BrowserContext`` with its own cookie jar
    and the exact options it was created with, so a guard can assert every
    role got identical context options and that every context opened during
    a run gets closed."""

    def __init__(self, kwargs: dict):
        self.kwargs = kwargs
        self.cookies: dict[str, str] = {}
        self.closed = False

    def new_page(self):
        return _SessionFakePage(self)

    def close(self):
        self.closed = True


class _SessionFakeBrowser:
    """Stand-in for a Playwright ``Browser`` recording every
    ``new_context()`` call — a role-keyed context per role vs. one context
    shared by every role is exactly what distinguishes the fixed command
    from the pre-fix one."""

    def __init__(self):
        self.contexts: list[_SessionFakeContext] = []

    def new_context(self, **kwargs):
        context = _SessionFakeContext(kwargs)
        self.contexts.append(context)
        return context

    def close(self):
        pass


@pytest.mark.django_db
class TestHandleIsolatesARolesCookieAcrossAnotherRolesLogin:
    """Refs #2325 (follow-up review): ``handle()`` is the one interface that
    kept its signature across the fix, so it is driven end-to-end here
    through a fake Playwright module instead of calling the internal,
    signature-changed ``_run_lang_viewport`` directly.
    """

    def test_a_role_revisited_after_another_role_logs_in_keeps_its_own_cookie(self, tmp_path, monkeypatch):
        _NAVIGATION_LOG.clear()
        monkeypatch.setattr(Command, "_resolve_pks", lambda self: {})
        browser = _SessionFakeBrowser()
        monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: _FakeBrowserLauncher(browser))
        shots = [
            _role_route_shot("shot-a", "emma"),
            _role_route_shot("shot-b", "emma_1"),
            _role_route_shot("shot-c", "emma"),
        ]
        monkeypatch.setattr("core.management.commands.screenshot.SHOTS", shots)

        cmd = Command()
        cmd.handle(base_url="http://x", output_dir=str(tmp_path), only="", quality=82)

        # The third navigation revisits "emma" after "emma_1" logged in in
        # between; a context shared across roles would show "emma_1" here
        # because that role's login overwrote the shared cookie store.
        assert len(_NAVIGATION_LOG) == 3
        assert _NAVIGATION_LOG[2] == ("http://x/shot-c/", "emma")


@pytest.mark.django_db
class TestHandleGivesEveryRoleTheSameContextOptions:
    """Refs #2325 (follow-up review), guard (a): viewport, locale and
    ``ignore_https_errors`` must be identical across every per-role context
    created during a single language/viewport run."""

    def test_every_role_context_gets_identical_options(self, tmp_path, monkeypatch):
        _NAVIGATION_LOG.clear()
        monkeypatch.setattr(Command, "_resolve_pks", lambda self: {})
        browser = _SessionFakeBrowser()
        monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: _FakeBrowserLauncher(browser))
        shots = [_role_route_shot("shot-a", "emma"), _role_route_shot("shot-b", "emma_1")]
        monkeypatch.setattr("core.management.commands.screenshot.SHOTS", shots)

        cmd = Command()
        cmd.handle(base_url="http://x", output_dir=str(tmp_path), only="", quality=82)

        assert len(browser.contexts) == 2
        expected = {"viewport": DESKTOP, "locale": "de-DE", "ignore_https_errors": True}
        assert all(context.kwargs == expected for context in browser.contexts)


@pytest.mark.django_db
class TestHandleAppliesLanguageSwitchToSessionUsers:
    """Refs #2325 (follow-up review), guard (b): ``handle()`` drives the UI
    language purely through each seeded user's ``preferred_language`` — pin
    that it still updates every username in ``LANG_SWITCH_USERNAMES`` using
    real rows, the same verification approach already used throughout this
    file and the wider suite (e.g. ``TestResolvePksMirrorsDetailViewLiveFilters``
    above) rather than a reimplemented stand-in for the Django ORM.
    """

    def test_sets_preferred_language_for_every_lang_switch_username(self, tmp_path, monkeypatch, facility):
        for username in LANG_SWITCH_USERNAMES:
            User.objects.create_user(username=username, role=User.Role.LEAD, facility=facility)
        monkeypatch.setattr(Command, "_resolve_pks", lambda self: {})
        browser = _SessionFakeBrowser()
        monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: _FakeBrowserLauncher(browser))
        monkeypatch.setattr("core.management.commands.screenshot.SHOTS", [])

        cmd = Command()
        cmd.handle(base_url="http://x", output_dir=str(tmp_path), only="", quality=82)

        # handle() runs "de" then "en" in that order — the final value is
        # the last language it switched every lang-switch user to.
        for username in LANG_SWITCH_USERNAMES:
            assert User.objects.get(username=username).preferred_language == "en"


@pytest.mark.django_db
class TestHandleKeepsAnAnonymousShotIsolatedBetweenRoles:
    """Refs #2325 (follow-up review), guard (c): an anonymous shot
    (``role=None``) sitting between two authenticated roles — as the real
    gallery's public ``login`` shot does — must get its own context with no
    session cookie and must not disturb either role's own context."""

    def test_anonymous_shot_has_no_cookie_and_leaves_role_contexts_untouched(self, tmp_path, monkeypatch):
        _NAVIGATION_LOG.clear()
        monkeypatch.setattr(Command, "_resolve_pks", lambda self: {})
        browser = _SessionFakeBrowser()
        monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: _FakeBrowserLauncher(browser))
        shots = [
            _role_route_shot("shot-a", "emma"),
            _role_route_shot("shot-anon", None),
            _role_route_shot("shot-b", "emma_1"),
        ]
        monkeypatch.setattr("core.management.commands.screenshot.SHOTS", shots)

        cmd = Command()
        cmd.handle(base_url="http://x", output_dir=str(tmp_path), only="", quality=82)

        assert len(browser.contexts) == 3
        emma_context, anon_context, emma_1_context = browser.contexts
        assert anon_context.cookies.get("sessionid") is None
        assert emma_context.cookies.get("sessionid") == "emma"
        assert emma_1_context.cookies.get("sessionid") == "emma_1"


class _FailingSessionFakePage(_SessionFakePage):
    """Stand-in whose first non-login navigation raises a plain error —
    Refs #2325 (follow-up review), guard (d): every context opened during a
    run must still be closed even when a shot's own navigation blows up
    outright (not just a transient ``net::ERR_ABORTED``, which already has
    its own retry guard)."""

    def goto(self, url, timeout=None):
        if not url.endswith("/login/"):
            raise RuntimeError("boom")
        return super().goto(url, timeout=timeout)


class _FailingSessionFakeContext(_SessionFakeContext):
    def new_page(self):
        return _FailingSessionFakePage(self)


class _FailOnSecondRoleBrowser(_SessionFakeBrowser):
    """Variant of ``_SessionFakeBrowser`` whose second ``new_context()``
    call (the second role logged in during a run) hands back a context whose
    page blows up on its first real navigation."""

    def new_context(self, **kwargs):
        context = _FailingSessionFakeContext(kwargs) if len(self.contexts) == 1 else _SessionFakeContext(kwargs)
        self.contexts.append(context)
        return context


@pytest.mark.django_db
class TestHandleClosesEveryContextAfterAFailingNavigation:
    """Refs #2325 (follow-up review), guard (d): a shot whose navigation
    raises outright (not an aborted-navigation retry) must still count as
    failed and must not leave any context open, including contexts of roles
    unrelated to the failure."""

    def test_closes_every_context_and_counts_the_shot_as_failed(self, tmp_path, monkeypatch):
        _NAVIGATION_LOG.clear()
        monkeypatch.setattr(Command, "_resolve_pks", lambda self: {})
        browser = _FailOnSecondRoleBrowser()
        monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: _FakeBrowserLauncher(browser))
        shots = [
            _role_route_shot("shot-a", "emma"),
            _role_route_shot("shot-b", "emma_1"),
            _role_route_shot("shot-c", "emma"),
        ]
        monkeypatch.setattr("core.management.commands.screenshot.SHOTS", shots)

        cmd = Command()
        with pytest.raises(CommandError, match="screenshot"):
            cmd.handle(base_url="http://x", output_dir=str(tmp_path), only="", quality=82)

        # Two contexts: "emma" (reused for shot-a and shot-c) and "emma_1"
        # (whose only shot raises). Both must be closed regardless.
        assert len(browser.contexts) == 2
        assert all(context.closed for context in browser.contexts)
