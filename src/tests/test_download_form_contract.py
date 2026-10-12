"""Download forms opt into timed duplicate-submit protection."""

from django.template.loader import render_to_string


def test_download_forms_opt_in_to_timed_release():
    for variant in ("internal", "reports"):
        html = render_to_string("core/statistics/partials/_export_bar.html", {"variant": variant})
        assert "data-download-form" in html
