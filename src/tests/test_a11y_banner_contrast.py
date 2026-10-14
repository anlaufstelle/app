"""Banner contrast from Tailwind OKLCH tokens, including inherited text."""

import math
import re
from pathlib import Path

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from tests.test_a11y_board_navigation import TreeParser, find

STATIC = Path(__file__).resolve().parents[1] / "static" / "css"
TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
TOKENS = {
    "yellow-500": (0.795, 0.184, 86.047),
    "blue-500": (0.623, 0.214, 259.815),
    "red-500": (0.637, 0.237, 25.331),
    "yellow-800": (0.476, 0.114, 61.907),
    "blue-800": (0.424, 0.199, 265.638),
    "red-800": (0.444, 0.177, 26.899),
    "accent": (0.44, 0.11, 168),
    "ink": (0.18, 0.01, 75),
    "surface": (1, 0, 0),
    "white": (1, 0, 0),
}


def luminance(token):
    lightness, chroma, hue = TOKENS[token]
    a = chroma * math.cos(math.radians(hue))
    b = chroma * math.sin(math.radians(hue))
    small_l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    small_m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    small_s = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    linear = [
        4.0767416621 * small_l - 3.3077115913 * small_m + 0.2309699292 * small_s,
        -1.2684380046 * small_l + 2.6097574011 * small_m - 0.3413193965 * small_s,
        -0.0041960863 * small_l - 0.7034186147 * small_m + 1.7076147010 * small_s,
    ]
    return sum(
        weight * min(1, max(0, channel)) for weight, channel in zip((0.2126, 0.7152, 0.0722), linear, strict=True)
    )


def contrast(foreground, background):
    values = sorted((luminance(foreground), luminance(background)))
    return (values[1] + 0.05) / (values[0] + 0.05)


def verify_built_token(token):
    css_path = STATIC / "styles.css"
    if not css_path.exists():
        pytest.skip("styles.css nicht gebaut — vorher make tailwind-build ausführen")
    css = css_path.read_text()
    if token in {"white", "surface"}:
        variable = "--color-white" if token == "white" else "--surface"
        assert re.search(re.escape(variable) + r":\s*#fff(?:fff)?[;}]", css)
        return
    variable = {"ink": "--text-primary", "accent": "--accent"}.get(token, "--color-" + token)
    match = re.search(re.escape(variable) + r":\s*oklch\(([^)]+)\)", css)
    assert match, f"Missing built token: {token}"
    parts = match[1].split()
    actual = (float(parts[0].rstrip("%")) / (100 if "%" in parts[0] else 1), float(parts[1]), float(parts[2]))
    assert actual == pytest.approx(TOKENS[token]), f"Built token drift: {token}"


def color_pairs(node, foreground, background):
    for cls in node.get("class", "").split():
        if cls.startswith("bg-"):
            assert cls[3:] in TOKENS, f"Unmeasured background: {cls}"
            background = cls[3:]
        if cls.startswith("text-"):
            if cls[5:] in TOKENS:
                foreground = cls[5:]
            else:
                assert cls in {"text-sm", "text-xs", "text-center", "text-left", "text-[12px]", "text-[13px]"}, (
                    f"Unmeasured text color: {cls}"
                )
    yield foreground, background
    for child in node:
        yield from color_pairs(child, foreground, background)


@pytest.mark.django_db
@pytest.mark.parametrize("banner", ["offline-banner", "sync-banner", "conflict-banner"])
def test_banner_text_links_counters_and_help_meet_contrast(banner, client, admin_user):
    client.force_login(admin_user)
    response = client.get(reverse("core:client_list"))
    sources = [response.content.decode(), render_to_string("offline_base.html", request=response.wsgi_request)]
    roots = [find(TreeParser(source).root, "data-testid", banner) for source in sources]
    assert roots[0].get("class") == roots[1].get("class")
    for root in roots:
        for foreground, background in color_pairs(root, "ink", "surface"):
            ratio = contrast(foreground, background)
            assert ratio >= 4.5, f"{banner}: {foreground}/{background} = {ratio:.3f}:1"


def test_built_banner_tokens_match_contrast_inputs():
    for token in ("yellow-800", "blue-800", "red-800", "white", "surface", "ink", "accent"):
        verify_built_token(token)


def test_context_help_design_token_fallbacks_match_source():
    source = (STATIC / "input.css").read_text()
    assert "--accent: oklch(0.44 0.11 168)" in source
    assert "--text-primary: oklch(0.18 0.01 75)" in source
    assert "--surface: #fff" in source


@pytest.mark.django_db
def test_contrast_calculation_runs_without_built_css(tmp_path, monkeypatch, client, admin_user):
    monkeypatch.setattr("tests.test_a11y_banner_contrast.STATIC", tmp_path)
    for banner in ("offline-banner", "sync-banner", "conflict-banner"):
        test_banner_text_links_counters_and_help_meet_contrast(banner, client, admin_user)


def test_missing_built_css_reports_the_build_prerequisite(tmp_path, monkeypatch):
    monkeypatch.setattr("tests.test_a11y_banner_contrast.STATIC", tmp_path)
    with pytest.raises(pytest.skip.Exception, match="styles.css.*make tailwind-build"):
        verify_built_token("red-800")


def test_changed_built_token_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("tests.test_a11y_banner_contrast.STATIC", tmp_path)
    (tmp_path / "styles.css").write_text("--color-red-800:oklch(0.8 0.177 26.899);")
    with pytest.raises(AssertionError, match="Built token drift: red-800"):
        verify_built_token("red-800")
