"""The retry link of the standalone maintenance page meets the AA text contrast in every state."""

import re

import pytest
from django.template.loader import render_to_string

MIN_CONTRAST = 4.5


def _luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _declarations(css: str, selector: str) -> dict[str, str]:
    """Merge the declarations of every rule whose selector list contains exactly ``selector``."""
    merged: dict[str, str] = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if selector in [s.strip() for s in selectors.split(",")]:
            for prop, value in re.findall(r"([\w-]+)\s*:\s*([^;]+);?", body):
                merged[prop] = value.strip()
    return merged


@pytest.fixture(scope="module")
def css() -> str:
    html = render_to_string("503.html")
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.DOTALL)
    assert blocks
    return "\n".join(blocks)


@pytest.mark.parametrize("state", ["", ":hover", ":focus-visible"])
def test_retry_link_text_contrast_in_every_state(css, state):
    base = _declarations(css, ".reload")
    declared = {**base, **_declarations(css, f".reload{state}")} if state else base
    background = declared["background-color"]
    foreground = declared["color"]

    assert _contrast(foreground, background) >= MIN_CONTRAST, (state, foreground, background)


def test_retry_link_focus_stays_visible(css):
    focus = _declarations(css, ".reload:focus-visible")

    # The ring must not rely on the background change alone and must not be suppressed.
    outline = focus.get("outline", "none").split()
    assert outline[0] not in ("none", "0")
    assert _contrast(outline[-1], "#ffffff") >= 3.0
