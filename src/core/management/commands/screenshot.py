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
from dataclasses import dataclass
from pathlib import Path

# Playwright's sync API runs an event loop; Django's ORM then refuses to run in
# that "async" context. This is a local dev/e2e screenshot tool, so we opt out of
# the guard rather than thread every ORM call through sync_to_async.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")

from django.core.management.base import BaseCommand, CommandError
from django.urls import NoReverseMatch, reverse

from core.models import Case, Client, User

SEED_PASSWORD = "anlaufstelle2026"  # noqa: S105 — seed-only credential (dev/e2e)

DESKTOP = {"width": 1280, "height": 800}
MOBILE = {"width": 375, "height": 812}


@dataclass
class Shot:
    """One screenshot target."""

    name: str  # base filename (DE), e.g. "zeitstrom"
    route: str  # reverse() name or literal path starting with "/"
    role: str | None = "thomas"  # seed username to log in as; None = anonymous
    langs: tuple[str, ...] = ("de", "en")
    mobile: bool = False  # also render a 375px mobile variant
    full_page: bool = False
    wait: str = "#main-content"  # selector to await before shooting
    pk_kind: str | None = None  # "client" | "case" → fill route kwargs from the DB


# Highlights (README) + full gallery (docs/screenshots.md). Only shipped
# features — no unreleased-milestone screens.
SHOTS: list[Shot] = [
    # --- Highlights ---
    Shot("zeitstrom", "core:zeitstrom", mobile=True),
    Shot("personenliste", "core:client_list"),
    Shot("statistiken", "core:statistics"),
    Shot("personendetail", "core:client_detail", pk_kind="client", full_page=True),
    Shot("arbeitszentrale", "core:dashboard", mobile=True),
    # --- Gallery ---
    Shot("login", "/login/", role=None, langs=("de",)),
    Shot("ereignis-anlegen", "core:event_create"),
    Shot("fallakte", "core:case_detail", pk_kind="case", full_page=True),
    Shot("uebergabe", "/?view=uebergabe"),
    Shot("statistik-extern", "core:statistics_external_report"),
    Shot("dsgvo-paket", "core:dsgvo_package"),
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
                User.objects.filter(username__in=["thomas", "superadmin"]).update(preferred_language=lang)
                for viewport, is_mobile in ((DESKTOP, False), (MOBILE, True)):
                    locale = "de-DE" if lang == "de" else "en-US"
                    context = browser.new_context(viewport=viewport, locale=locale, ignore_https_errors=True)
                    o, f = self._run_lang_viewport(context, base_url, out_dir, lang, is_mobile, shots, pks, quality)
                    ok += o
                    fail += f
                    context.close()
            browser.close()

        self.stdout.write(self.style.SUCCESS(f"Screenshots erzeugt: {ok} ok / {fail} fehlgeschlagen — siehe {out_dir}"))

    def _resolve_pks(self) -> dict:
        """Pick representative objects from the (e2e) DB for detail routes."""
        thomas = User.objects.filter(username="thomas").first()
        facility = getattr(thomas, "facility", None)
        client = Client.objects.filter(facility=facility).order_by("pk").first() if facility else None
        case = Case.objects.filter(facility=facility).order_by("pk").first() if facility else None
        return {"client": client, "case": case}

    def _url(self, base_url: str, shot: Shot, pks: dict) -> str | None:
        if shot.route.startswith("/"):
            return base_url + shot.route
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

    def _run_lang_viewport(self, context, base_url, out_dir, lang, is_mobile, shots, pks, quality):
        from PIL import Image

        ok, fail = 0, 0
        sessions: dict[str | None, object] = {}

        def page_for(role):
            if role not in sessions:
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
                page.goto(url, timeout=45000)
                page.wait_for_load_state("domcontentloaded")
                with contextlib.suppress(Exception):
                    page.wait_for_selector(shot.wait, timeout=8000)
                png = page.screenshot(full_page=shot.full_page)
                path = out_dir / self._filename(shot.name, lang, is_mobile)
                Image.open(io.BytesIO(png)).convert("RGB").save(path, "WEBP", quality=quality, method=6)
                self.stdout.write(f"  ✓ {path.name}")
                ok += 1
            except Exception as exc:  # noqa: BLE001 — keep going on a single bad shot
                self.stdout.write(self.style.ERROR(f"  ✗ {shot.name} ({lang}, mobile={is_mobile}): {exc}"))
                fail += 1
        return ok, fail

    @staticmethod
    def _filename(name: str, lang: str, is_mobile: bool) -> str:
        suffix = "-mobil" if is_mobile else ""
        lang_suffix = "" if lang == "de" else "-en"
        return f"{name}{suffix}{lang_suffix}.webp"
