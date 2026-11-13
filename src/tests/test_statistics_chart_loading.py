"""The chart library remains identifiable with production manifest storage."""

from html.parser import HTMLParser
from unittest.mock import patch

from django.template import Context
from django.template.loader import get_template
from django.template.loader_tags import BlockNode
from django.test import override_settings


class ScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.scripts.append(dict(attrs))


def test_manifest_chart_library_tag_keeps_its_marker(settings):
    manifest = {
        "js/chart.min.js": "js/chart.min.aa284506853a.js",
        "js/statistics-charts.js": "js/statistics-charts.0123456789ab.js",
    }
    storages = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }
    with (
        override_settings(DEBUG=False, STORAGES=storages),
        patch("django.contrib.staticfiles.storage.ManifestFilesMixin.load_manifest", return_value=(manifest, "test")),
    ):
        template = get_template("core/statistics/index.html").template
        head = next(block for block in template.nodelist.get_nodes_by_type(BlockNode) if block.name == "extra_head")
        parser = ScriptParser()
        parser.feed(head.render(Context()))

    library = next(script for script in parser.scripts if "chart.min." in script.get("src", ""))
    assert library["src"].endswith("/js/chart.min.aa284506853a.js")
    assert "defer" in library
    assert "data-chart-library" in library
