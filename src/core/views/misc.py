"""Kleine, verteilte Views ohne eigenes Feature-Modul (Refs #671).

Aktuell: ``RobotsTxtView`` — liefert die ``robots.txt`` mit
Disallow-fuer-alles. Caddy setzt zusaetzlich ``X-Robots-Tag: noindex``
(Defense-in-Depth, Caddyfile.dev).
"""

from django.views.generic import TemplateView


class RobotsTxtView(TemplateView):
    """GET /robots.txt — schliesst Crawler von der Indexierung aus."""

    template_name = "robots.txt"
    content_type = "text/plain"
