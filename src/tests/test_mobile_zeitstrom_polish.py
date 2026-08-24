"""Mobile Zeitstrom-/PWA-Politur aus dem RC-Befund (Refs #1947)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from django.template.loader import render_to_string

ROOT = Path(__file__).resolve().parents[2]


def _classes_for_testid(html: str, testid: str) -> set[str]:
    tag = re.search(rf'<[^>]+data-testid="{re.escape(testid)}"[^>]*>', html)
    assert tag, f"data-testid={testid!r} fehlt"
    classes = re.search(r'class="([^"]+)"', tag.group(0))
    assert classes, f"class fehlt an data-testid={testid!r}"
    return set(classes.group(1).split())


def test_tally_action_has_mobile_44px_touch_target():
    row = {
        "document_type": SimpleNamespace(pk="00000000-0000-0000-0000-000000000001", name="Kontakt"),
        "requires_number_input": True,
        "tally_increment_url": "/partials/tally/increment/?document_type=1",
    }

    html = render_to_string("core/zeitstrom/partials/tally_action.html", {"row": row})

    classes = _classes_for_testid(html, "tally-increment-00000000-0000-0000-0000-000000000001")
    assert {"min-h-[44px]", "min-w-[44px]"} <= classes


def test_time_filter_tabs_have_mobile_touch_height_and_scroll_hint():
    time_filter = SimpleNamespace(
        pk="00000000-0000-0000-0000-000000000002",
        label="Spätdienst",
        start_time=None,
        end_time=None,
    )

    html = render_to_string(
        "components/_time_filter_dropdown.html",
        {
            "time_filters": [time_filter],
            "selected_filter": None,
            "selected_filter_id": "all",
            "target_date": None,
            "events_partial_url": "core:zeitstrom_feed_partial",
            "feed_target_id": "feed-list",
        },
    )

    assert 'data-testid="time-filter-scroll-hint"' in html
    assert "pointer-events-none" in _classes_for_testid(html, "time-filter-scroll-hint")
    assert "min-h-[44px]" in _classes_for_testid(html, "time-filter-all")
    assert "min-h-[44px]" in _classes_for_testid(html, "time-filter-spatdienst")


def test_zeitstrom_subnav_chips_have_mobile_touch_height():
    source = (ROOT / "src/templates/core/zeitstrom/index.html").read_text()

    for testid in ("zeitstrom-tab-feed", "zeitstrom-tab-uebergabe", "zeitstrom-filter-chip"):
        classes = _classes_for_testid(source, testid)
        assert "min-h-[44px]" in classes, testid


def test_manifest_and_meta_tags_share_the_real_brand_colors():
    manifest = json.loads((ROOT / "src/static/manifest.json").read_text())

    assert manifest["theme_color"] == "#006547"
    assert manifest["background_color"] == "#f7f3ec"
    for template in ("base.html", "auth/login.html", "offline_base.html"):
        source = (ROOT / "src/templates" / template).read_text()
        assert '<meta name="theme-color" content="#006547">' in source, template
