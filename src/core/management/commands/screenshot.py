"""Generate documentation screenshots (DE + EN, desktop + mobile).

Drives a running **E2E server** (default ``http://127.0.0.1:8844``) with
Playwright, logs in as seeded users, switches the UI language via each user's
``preferred_language`` and writes **WebP** screenshots to ``docs/screenshots/``.

Refs #1003, #1005.

Prerequisites:
    1. Seed + start the E2E server (see ``docs/e2e-runbook.md`` §6), ideally with
       ``--scale=medium`` so every screen has data.
    2. Run against that server::

        DJANGO_SETTINGS_MODULE=anlaufstelle.settings.e2e \\
          .venv/bin/python src/manage.py screenshot --base-url http://127.0.0.1:8844

The command is resilient: a failing shot is logged and skipped so a partial run
still produces the other images. The ``make docs-screens`` target wraps the full
pipeline (seed → server → generate → stop).
"""

from __future__ import annotations

import contextlib
import io
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

# Refs #1730: nur fuer mypy — playwright ist eine optionale Dev/E2E-Abhaengigkeit
# und wird unten in ``handle()`` lazy importiert (Guard gegen ``ImportError`` in
# Umgebungen ohne playwright). Der ``TYPE_CHECKING``-Import laedt zur Laufzeit nie.
if TYPE_CHECKING:
    from playwright.sync_api import ViewportSize

# Playwright's sync API runs an event loop; Django's ORM then refuses to run in
# that "async" context. This is a local dev/e2e screenshot tool, so we opt out of
# the guard rather than thread every ORM call through sync_to_async.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")

from django.core.management.base import BaseCommand, CommandError
from django.urls import NoReverseMatch, reverse

from core.models import Client, User
from core.services.case import cases_with_live_client

SEED_PASSWORD = "anlaufstelle2026"  # noqa: S105 — seed-only credential (dev/e2e)

# Seed-Usernamen, deren `preferred_language` je Sprachdurchlauf umgestellt wird.
# Refs #1595: "admin" ergaenzt, weil der dsgvo-paket-Shot als FacilityAdmin
# laeuft und sonst auch im EN-Durchlauf deutsch rendert.
# Refs #2148: "emma_1" (Zweigstelle Nord/Beratungsstelle) ergaenzt fuer den
# Galerie-Shot "ereignis-anlegen-beratungsstelle" — sonst rendert dessen
# EN-Durchlauf deutsch, weil der Login-User keine eigene Sprachpraeferenz hat.
LANG_SWITCH_USERNAMES = ("emma", "emma_1", "superadmin", "admin")

DESKTOP: ViewportSize = {"width": 1280, "height": 800}
MOBILE: ViewportSize = {"width": 375, "height": 812}


@dataclass
class Shot:
    """One screenshot target."""

    name: str  # base filename (DE), e.g. "zeitstrom"
    route: str  # reverse() name or literal path starting with "/"
    role: str | None = "emma"  # seed username to log in as; None = anonymous
    langs: tuple[str, ...] = ("de", "en")
    mobile: bool = False  # also render a 375px mobile variant
    full_page: bool = False
    wait: str = "#main-content"  # selector to await before shooting
    pk_kind: str | None = None  # "client" | "case" → fill route kwargs from the DB
    # Refs #1594: optional page.wait_for_function() expression — for pages that
    # render async (e.g. Chart.js) where the `wait` selector is present in the
    # DOM before the content inside it is actually drawn. Best-effort like `wait`.
    wait_js: str | None = None


# Highlights (README) + full gallery (docs/screenshots.md). Only shipped
# features — no unreleased-milestone screens.
SHOTS: list[Shot] = [
    # --- Highlights ---
    # ?time_filter=all: "Alle"-Tab statt der uhrzeitabhaengigen aktuellen
    # Schicht — sonst zeigt ein Nacht-Lauf einen leeren Nachtdienst-Feed.
    Shot("zeitstrom", "/?time_filter=all", mobile=True),
    Shot("personenliste", "core:client_list"),
    # Refs #1594: die Charts werden clientseitig async gezeichnet — der
    # #main-content-Selektor ist sofort da, die Canvases aber noch leer.
    Shot(
        "statistiken",
        "core:statistics",
        wait_js="document.body.getAttribute('data-charts-ready')==='true'",
    ),
    Shot("personendetail", "core:client_detail", pk_kind="client", full_page=True),
    Shot("arbeitszentrale", "core:dashboard", mobile=True),
    # --- Gallery ---
    Shot("login", "/login/", role=None, langs=("de",)),
    Shot("ereignis-anlegen", "core:event_create"),
    # Refs #2148: einziger Shot, der den Paket-Unterschied zeigt (Typwahl
    # Kurzberatung/Beratungsgespraech/Krisengespraech/Begleitung aus dem Paket
    # Beratungsstelle) — Zweigstelle Nord, Login "emma_1" (Maintainer-Entscheid
    # 2026-09-06, siehe docs/screenshots.md "Alltag & Dokumentation").
    Shot("ereignis-anlegen-beratungsstelle", "core:event_create", role="emma_1"),
    Shot("fallakte", "core:case_detail", pk_kind="case", full_page=True),
    Shot("uebergabe", "/?view=uebergabe"),
    # Refs #2505: the former external report page is the "Allgemeine Kennzahlen"
    # report card now. The shot keeps its name: docs/screenshots.md links the file.
    Shot("statistik-extern", "/statistics/?ansicht=datensaetze&bericht=general-key-figures"),
    # Refs #1595: FacilityAdminRequiredMixin verlangt mehr als die Default-Rolle
    # "emma" (LEAD) — sonst 403 statt der DSGVO-Paket-Uebersicht.
    Shot("dsgvo-paket", "core:dsgvo_package", role="admin"),
    # --- Neue Flaechen aus #1803/#1813 (Refs #1843/#1900) ---
    # Beide Hubs als admin: /verwaltung/ sitzt hinter FacilityAdminRequiredMixin,
    # und die Statuslisten beider Uebersichten richten sich an die
    # Anwendungsbetreuung (Muster dsgvo-paket, Refs #1595).
    Shot("datenschutz-uebersicht", "core:datenschutz_uebersicht", role="admin"),
    Shot("verwaltung-uebersicht", "core:verwaltung_uebersicht", role="admin"),
    # Reiterzeile + Berichtskarten des Datensatz-Berichte-Reiters; die Adresse
    # ist seit #1804 deutsch (?ansicht=datensaetze, alte Schreibweise bleibt
    # still akzeptiert).
    Shot("statistik-berichte", "/statistics/?ansicht=datensaetze"),
    # /search/-Vollseite: die Query kommt aus dem ersten Seed-Client (der
    # Bulk-Seed wuerfelt Pseudonyme); der Wortteil vor dem Suffix liefert
    # mehrere Treffer statt genau einem.
    Shot("suche", "/search/?q={client_pseudonym}", full_page=True),
    # NOTE: /system/-Screens (compliance, systembereich) brauchen Sudo-Mode und
    # sind hier (noch) ausgeklammert — Follow-up.
]


class Command(BaseCommand):
    help = "Generate DE/EN desktop+mobile WebP screenshots from a running E2E server."

    def add_arguments(self, parser):
        parser.add_argument("--base-url", default="https://127.0.0.1:8443")
        parser.add_argument("--output-dir", default="docs/screenshots")
        parser.add_argument("--only", default="", help="comma-separated shot names to render")
        parser.add_argument("--quality", type=int, default=82)

    def handle(self, *args, **opts):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise CommandError("playwright nicht installiert (requirements-dev.txt)") from exc
        try:
            import PIL  # noqa: F401 — presence check; imported lazily where used
        except ImportError as exc:  # pragma: no cover
            raise CommandError("Pillow nicht installiert — WebP-Konvertierung nicht möglich") from exc

        base_url = opts["base_url"].rstrip("/")
        out_dir = Path(opts["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        quality = opts["quality"]
        only = {s.strip() for s in opts["only"].split(",") if s.strip()}

        pks = self._resolve_pks()
        shots = [s for s in SHOTS if not only or s.name in only]

        ok, fail = 0, 0
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for lang in ("de", "en"):
                # Drive the UI language through the seeded users' preference.
                User.objects.filter(username__in=LANG_SWITCH_USERNAMES).update(preferred_language=lang)
                for viewport, is_mobile in ((DESKTOP, False), (MOBILE, True)):
                    locale = "de-DE" if lang == "de" else "en-US"
                    context_kwargs = {"viewport": viewport, "locale": locale, "ignore_https_errors": True}
                    o, f = self._run_lang_viewport(
                        browser, context_kwargs, base_url, out_dir, lang, is_mobile, shots, pks, quality
                    )
                    ok += o
                    fail += f
            browser.close()

        self.stdout.write(self.style.SUCCESS(f"Screenshots erzeugt: {ok} ok / {fail} fehlgeschlagen — siehe {out_dir}"))
        # Refs #2325: a non-zero exit code is what turns `make docs-screens`
        # red — without it, a photographed error page passed silently.
        if fail > 0:
            raise CommandError(f"{fail} screenshot(s) failed")

    def _resolve_pks(self) -> dict:
        """Pick representative objects from the (e2e) DB for detail routes."""
        emma = User.objects.filter(username="emma").first()
        facility = getattr(emma, "facility", None)
        # Refs #2325: mirror the detail views' live-object filters here, or a
        # trashed/anonymized row at the lowest pk gets screenshotted as a 404.
        client = Client.objects.active().filter(facility=facility).order_by("pk").first() if facility else None
        case = cases_with_live_client().filter(facility=facility).order_by("pk").first() if facility else None
        return {"client": client, "case": case}

    def _url(self, base_url: str, shot: Shot, pks: dict) -> str | None:
        if shot.route.startswith("/"):
            path = shot.route
            if "{client_pseudonym}" in path:
                client = pks.get("client")
                if client is None:
                    return None
                path = path.replace("{client_pseudonym}", quote(client.pseudonym.split("-")[0]))
            return base_url + path
        kwargs = {}
        if shot.pk_kind:
            obj = pks.get(shot.pk_kind)
            if obj is None:
                return None
            kwargs = {"pk": obj.pk}
        for name in (shot.route, shot.route.split(":")[-1]):
            try:
                return base_url + reverse(name, kwargs=kwargs)
            except NoReverseMatch:
                continue
        return None

    def _run_lang_viewport(self, browser, context_kwargs, base_url, out_dir, lang, is_mobile, shots, pks, quality):
        from PIL import Image

        ok, fail = 0, 0
        sessions: dict[str | None, object] = {}
        contexts = []

        def page_for(role):
            # Refs #2325 (follow-up): pages created on the same browser
            # context share that context's cookies, including the session
            # cookie. A second role logging in on a page reused from the
            # first role's context would silently replace the first role's
            # session, so every role gets its own freshly created context.
            if role not in sessions:
                context = browser.new_context(**context_kwargs)
                contexts.append(context)
                page = context.new_page()
                if role is not None:
                    # Mirror the e2e ``_login`` helper (src/tests/e2e/conftest.py).
                    # First goto can be slow on a cold worker → one retry.
                    for attempt in range(2):
                        try:
                            page.goto(f"{base_url}/login/", timeout=45000)
                            break
                        except Exception:  # noqa: BLE001 — retry once on cold-start timeout
                            if attempt == 1:
                                raise
                    page.wait_for_load_state("domcontentloaded")
                    page.fill("input[name='username']", role)
                    page.fill("input[name='password']", SEED_PASSWORD)
                    page.locator("button[type='submit']").click()
                    with contextlib.suppress(Exception):
                        page.wait_for_url(lambda u: "/login" not in u, timeout=10000)
                    if "/login" in page.url:
                        self.stdout.write(
                            self.style.ERROR(f"  ! Login fehlgeschlagen für {role} (bleibt auf {page.url})")
                        )
                sessions[role] = page
            return sessions[role]

        try:
            for shot in shots:
                if is_mobile and not shot.mobile:
                    continue
                if lang not in shot.langs:
                    continue
                url = self._url(base_url, shot, pks)
                if url is None:
                    self.stdout.write(self.style.WARNING(f"  skip {shot.name} ({lang}, mobile={is_mobile}): keine URL"))
                    continue
                try:
                    page = page_for(shot.role)
                    try:
                        response = page.goto(url, timeout=45000)
                    except Exception as goto_exc:
                        # Refs #2325 (follow-up): aborted navigations on a reused
                        # page are transient; one retry, anything else counts as a failure.
                        if "net::ERR_ABORTED" not in str(goto_exc):
                            raise
                        time.sleep(1)
                        response = page.goto(url, timeout=45000)
                    page.wait_for_load_state("domcontentloaded")
                    # Refs #2325: an error status (e.g. a 404 for a missing seed
                    # object) must fail the shot instead of being saved as if it
                    # were real content.
                    if response is not None and not response.ok:
                        raise RuntimeError(f"HTTP {response.status} for {url}")
                    with contextlib.suppress(Exception):
                        page.wait_for_selector(shot.wait, timeout=8000)
                    if shot.wait_js:
                        # Best-effort like the selector wait above: a slow/failed
                        # async render still produces a (possibly incomplete) shot
                        # instead of aborting the whole run.
                        with contextlib.suppress(Exception):
                            page.wait_for_function(shot.wait_js, timeout=8000)
                    png = page.screenshot(full_page=shot.full_page)
                    path = out_dir / self._filename(shot.name, lang, is_mobile)
                    Image.open(io.BytesIO(png)).convert("RGB").save(path, "WEBP", quality=quality, method=6)
                    self.stdout.write(f"  ✓ {path.name}")
                    ok += 1
                except Exception as exc:  # noqa: BLE001 — keep going on a single bad shot
                    self.stdout.write(self.style.ERROR(f"  ✗ {shot.name} ({lang}, mobile={is_mobile}): {exc}"))
                    fail += 1
        finally:
            # Refs #2325 (follow-up): close every per-role context created
            # during this language/viewport run; the caller no longer holds a
            # single shared context to close itself.
            for context in contexts:
                context.close()
        return ok, fail

    @staticmethod
    def _filename(name: str, lang: str, is_mobile: bool) -> str:
        suffix = "-mobil" if is_mobile else ""
        lang_suffix = "" if lang == "de" else "-en"
        return f"{name}{suffix}{lang_suffix}.webp"
