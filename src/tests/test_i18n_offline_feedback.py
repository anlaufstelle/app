"""Delivered translations for offline feedback (Refs #1111)."""


def test_app_shells_deliver_translated_offline_feedback():
    """Both shells supply translated labels without a JavaScript catalogue."""
    from django.template.loader import render_to_string
    from django.utils.translation import override

    with override("en"):
        for template in ("base.html", "offline_base.html"):
            html = render_to_string(template, {})
            for label in (
                "Person is now available offline.",
                "Offline key is not active — please sign in again.",
                "Offline storage is unavailable.",
                "The server has changed again. Please review the conflict once more.",
            ):
                assert label in html
