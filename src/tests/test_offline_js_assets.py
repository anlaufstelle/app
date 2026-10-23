"""Template and precache contracts for minified offline scripts."""

import re
from pathlib import Path

import pytest

from tests.test_offline_js_build import SOURCES

pytestmark = pytest.mark.architecture
ROOT = Path(__file__).resolve().parents[2]


def test_templates_and_precache_use_same_minified_core_in_order():
    expected = [f"js/{name}.min.js" for name in SOURCES]
    sw = (ROOT / "src/static/js/sw.js").read_text()
    shell = sw.split("const APP_SHELL = [", 1)[1].split("];", 1)[0]
    precached = set(re.findall(r'"/static/(js/[^" ]+\.js)"', shell))
    referenced = set()
    for template in (ROOT / "src/templates").rglob("*.html"):
        scripts = re.findall(r"<script[^>]+src=\"{% static '([^']+)' %}\"", template.read_text())
        for name in SOURCES:
            assert f"js/{name}.js" not in scripts, str(template)
        referenced.update(set(scripts) & set(expected))
        if template.name in {"base.html", "offline_base.html"}:
            assert [script for script in scripts if script in expected] == expected, str(template)
    assert referenced == set(expected)
    assert precached & set(expected) == referenced
    assert not precached & {f"js/{name}.js" for name in SOURCES}
